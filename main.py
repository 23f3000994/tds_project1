# main.py
from fastapi import FastAPI, Request
import os, requests, base64, time
from typing import Optional, Tuple

app = FastAPI()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER")
SECRET = os.getenv("SECRET")

if not (GITHUB_TOKEN and GITHUB_USER and SECRET):
    print("⚠️ WARNING: One or more environment variables (GITHUB_TOKEN, GITHUB_USER, SECRET) are not set.")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": f"tds-bot/{GITHUB_USER}"
}

def gh_request(method: str, url: str, **kwargs):
    r = requests.request(method, url, headers=HEADERS, **kwargs)
    print(f"{method} {url} -> {r.status_code}")
    if r.text:
        print(r.text[:800])
    return r


def create_repo(repo_name: str) -> bool:
    url = "https://api.github.com/user/repos"
    payload = {
        "name": repo_name,
        "private": False,
        "auto_init": True,
        "license_template": "mit"
    }
    r = gh_request("POST", url, json=payload)
    if r.status_code == 201:
        return True
    if r.status_code == 422:
        try:
            body = r.json()
            errors = body.get("errors", [])
            for e in errors:
                if "already exists" in e.get("message", "").lower():
                    print("Repo already exists; continuing.")
                    return True
        except Exception:
            pass
    print("❌ Failed to create repo.")
    return False


def push_file(repo_name: str, filename="index.html", content="<h1>Hello World</h1>") -> Tuple[bool, Optional[str]]:
    """Create or update file contents."""
    url = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/contents/{filename}"
    encoded = base64.b64encode(content.encode()).decode()
    payload = {"message": f"Add or update {filename}", "content": encoded}
    r = gh_request("PUT", url, json=payload)
    if r.status_code in [200, 201]:
        try:
            data = r.json()
            sha = data.get("commit", {}).get("sha") or data.get("content", {}).get("sha")
            return True, sha
        except Exception:
            return True, None
    if r.status_code == 422:
        get_r = gh_request("GET", url)
        if get_r.status_code == 200:
            sha = get_r.json().get("sha")
            if sha:
                payload["sha"] = sha
                r2 = gh_request("PUT", url, json=payload)
                if r2.status_code in [200, 201]:
                    data = r2.json()
                    sha = data.get("commit", {}).get("sha") or data.get("content", {}).get("sha")
                    return True, sha
    return False, None


def enable_pages(repo_name: str) -> bool:
    url = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    payload = {"build_type": "legacy", "source": {"branch": "main", "path": "/"}}
    r = gh_request("POST", url, json=payload)
    return r.status_code in (201, 204)


def notify_evaluator(evaluation_url: str, payload: dict, max_retries=4) -> bool:
    """Notify evaluator endpoint with exponential backoff (2, 4, 8, 16 seconds)."""
    delays = [2, 4, 8, 16]
    for i, delay in enumerate(delays, start=1):
        try:
            r = requests.post(evaluation_url, json=payload, timeout=10)
            print("Evaluation API response:", r.status_code, r.text[:500])
            if 200 <= r.status_code < 300:
                print("✅ Evaluator notification successful.")
                return True
        except Exception as e:
            print("Notify exception:", e)
        print(f"⏳ Notify failed (attempt {i}/{len(delays)}). Retrying in {delay}s...")
        time.sleep(delay)
    print("❌ Evaluator notification failed after retries.")
    return False


@app.post("/")
async def handle(req: Request):
    data = await req.json()

    if data.get("secret") != SECRET:
        return {"status": "error", "reason": "Invalid secret"}

    task_id = data.get("task", "demo_task")
    repo_name = f"tds_{task_id}_{int(time.time()) % 10000}"

    if not create_repo(repo_name):
        return {"status": "error", "reason": "Failed to create repo"}

    html_content = f"""
    <!DOCTYPE html>
    <html><head><meta charset='utf-8'><title>{repo_name}</title></head>
    <body><h1>Hello from {repo_name}</h1><p>Auto-deployed with FastAPI.</p></body></html>
    """

    ok, sha_html = push_file(repo_name, "index.html", html_content)
    if not ok:
        return {"status": "error", "reason": "Failed to push index.html"}

    readme = f"# {repo_name}\n\nAuto-created for task {task_id}."
    ok, sha_readme = push_file(repo_name, "README.md", readme)
    if not ok:
        return {"status": "error", "reason": "Failed to push README.md"}

    mit_license = f"""MIT License

Copyright (c) {time.localtime().tm_year} {GITHUB_USER}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
"""
    push_file(repo_name, "LICENSE", mit_license)

    enable_pages(repo_name)
    pages_url = f"https://{GITHUB_USER}.github.io/{repo_name}/"

    payload = {
        "email": data.get("email"),
        "task": task_id,
        "repo": f"https://github.com/{GITHUB_USER}/{repo_name}",
        "pages_url": pages_url,
        "commit": sha_html or sha_readme,
    }

    evaluation_url = data.get("evaluation_url")
    if evaluation_url:
        notify_evaluator(evaluation_url, payload)

    return {
        "status": "success",
        "repo": payload["repo"],
        "pages_url": pages_url,
        "commit": payload["commit"]
    }
