# The Software Factory — A Beginner's Guide

*Versión en español: [GUIA-PRINCIPIANTES.md](GUIA-PRINCIPIANTES.md)*

This guide is written for people who are **not professional programmers**. If you can
install a program and copy-paste text into a window, you can use the Software Factory.

---

## 1. What is this, in plain words?

Imagine a small workshop staffed by tireless assistants:

- A **planner** reads your request and splits it into small jobs.
- Several **builders** do those jobs at the same time, each on their own copy so they
  never step on each other.
- An **inspector** checks the quality of the work (does it run? are there mistakes?).
- A **security guard** checks nothing dangerous slipped in.
- A **repair person** fixes problems automatically when a check fails.
- A **dashboard** on your screen shows you everything they did today.

Those assistants are AI agents. You write **what** you want in normal language —
"add a page that shows a welcome message" — and the factory does the **how**.

There is exactly one thing the factory is never allowed to do alone: **publish
(deploy) the application**. A human — you — must always press the approve button.

## 2. What you need before starting (one-time setup)

You need two programs and one key. The programs are free; the key comes with an
account at an AI provider — you choose which one.

### 2.1 Install Python (the engine)

1. Go to https://www.python.org/downloads/ and click the yellow download button.
2. Run the installer. **Important:** on the first screen, tick the box
   "**Add Python to PATH**" before clicking Install.

### 2.2 Install Git (the filing cabinet)

1. Go to https://git-scm.com/downloads and download the Windows version.
2. Run the installer and click "Next" on every screen. The defaults are fine.

### 2.3 Get the factory's brain: an AI provider key

The factory's assistants are powered by an AI provider. It currently works with
**two providers — pick whichever you prefer** (you only need one):

| Provider | Where to get the key | The key looks like |
|---|---|---|
| **Cursor** | https://cursor.com → Dashboard → Integrations → API key | `cursor_abc123...` |
| **Anthropic (Claude)** | https://console.anthropic.com → API keys → Create key | `sk-ant-abc123...` |

Copy the key somewhere safe. Treat it like a password: don't share it or post it
anywhere. (The factory is built so that other providers can be added over time —
if your institution uses a different one, ask a technical colleague about it.)

### 2.4 Open a terminal

Everything below happens in a **terminal** — a window where you type commands.
On Windows: press the **Start** key, type `powershell`, press Enter. A blue window
opens. That's it. You copy a command from this guide, paste it there (right-click
pastes), and press Enter.

## 3. Get your own copy of the factory

1. Open https://github.com/chhdeza/My-SW-Factory in your browser.
2. Click the green **"Use this template"** button → **"Create a new repository"**.
   (You need a free GitHub account — create one at https://github.com/signup.)
3. Give it a name, for example `my-first-app`, and click **Create repository**.
4. Now copy it to your computer. In the terminal, paste (replace YOUR-USER):

```powershell
cd $HOME\Documents
git clone https://github.com/YOUR-USER/my-first-app
cd my-first-app
```

## 4. Install the factory (5 minutes, one time per project)

Paste these three commands one at a time, pressing Enter after each.
In the third command, use the name of the provider you chose in step 2.3 —
`cursor` or `claude`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,cursor]"     # or:  pip install -e ".[dev,claude]"
```

> If the second command complains about "execution policy", run this once and retry:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

What did that do? It created a private toolbox for this project (`venv`), switched
into it, and installed the factory inside.

## 5. Wake up the factory

```powershell
factory init
```

The factory asks you a few questions. **Press Enter to accept every default**, except:

- When it asks for the **default agent provider**, type the one you chose in
  step 2.3: `cursor` or `claude`.
- When it asks for the matching key — **CURSOR_API_KEY** or **ANTHROPIC_API_KEY** —
  paste the key from step 2.3. (Leave the other one empty by pressing Enter.)
- When it asks *"Install the open-source gate tools now?"* answer **y** (yes).
  This installs the free inspection tools so quality and security checks work.

That's it. The factory is awake.

## 6. Create your first application

The factory needs a small seed to grow from. Let's plant one — a tiny web app.

Create a file called `app.py` in the project folder (you can use Notepad:
`notepad app.py` in the terminal) with this content:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {"message": "Hello from our university!"}
```

And a file called `test_app.py`:

```python
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_root():
    assert client.get("/").json() == {"message": "Hello from our university!"}
```

Save both, then tell the filing cabinet about them:

```powershell
git add app.py test_app.py
git commit -m "feat: my first application"
```

## 7. Ask the factory to build a new feature

This is the magic moment. Type:

```powershell
factory run "Add a page at /welcome that returns a welcome message with the university name, and include a test"
```

Now wait 2–10 minutes and watch. The factory will:

1. **Plan** — split your request into jobs.
2. **Build** — two or more AI builders write the code in parallel.
3. **Inspect** — run the quality checks (code style, tests) and security checks.
4. **Repair** — if a check fails, the repair agent tries to fix it by itself.
5. **Deliver** — if everything passes and the change is low-risk, it merges the
   new feature into your application automatically.

When it finishes you'll see a message like `task task-xxxx: merged ... (low risk)`.

**See your new feature run:**

```powershell
pip install uvicorn
uvicorn app:app
```

Open http://127.0.0.1:8000/welcome in your browser. There's your page — written,
tested, and checked without you writing a line of code. Press `Ctrl+C` in the
terminal to stop it.

## 8. Watch the factory work: the dashboard

```powershell
factory dashboard
```

Open http://localhost:8700 in your browser. You'll see:

- **Today's numbers**: how many AI agents ran, how much it cost (estimated),
  how many fixes and deliveries happened.
- **Tasks**: everything you've asked for and its current state.
- **Agent runs**: each individual AI worker and what it did.
- **Traces**: click one to read the full conversation with the AI (passwords and
  keys are automatically blacked out).
- **Deploy**: the approval button — nothing publishes without your click.

Press `Ctrl+C` in the terminal to stop the dashboard.

## 9. Ask for more features — the everyday routine

From now on, your workflow is just this loop:

```powershell
factory run "describe what you want, in plain language"
```

Tips for good requests:

- **Be specific.** "Add a page at /schedule that shows a list of course names" works
  better than "make it better".
- **One feature at a time.** Small requests succeed more often and cost less.
- **Always mention tests.** End with "and include a test" — the factory then proves
  its own work.

If the factory says a change was **"held for human review"**, that's a safety
feature, not an error: the change looked risky (too big, touched sensitive files),
so it's waiting for a human to look at it.

## 10. If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `CURSOR_API_KEY is not set` / `ANTHROPIC_API_KEY is not set` | The factory can't find your provider key | Run `factory init` again and paste the key |
| `gates failed after self-heal` | The checks failed and auto-repair couldn't fix it | Try the request again with a simpler description |
| `budget exceeded` | The task hit its spending limit (a protection) | Raise the limit in `factory.yaml` or simplify the request |
| The terminal says `factory` is not recognized | The toolbox isn't active | Run `.venv\Scripts\Activate.ps1` first |

## 11. What it costs

The factory itself is free and open source. The only cost is the AI usage billed by
your provider (Cursor or Anthropic). The factory has built-in spending limits (about $5 per task and $25
per day by default) and the dashboard shows the estimated spend, so there are no
surprises. A small feature typically costs a few cents to a few dozen cents.

---

*Questions or ideas? Open an "Issue" on the GitHub page of the project — that is the
public suggestion box.*
