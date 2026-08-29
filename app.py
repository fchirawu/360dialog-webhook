# Changes to make to app.py on GitHub

## 1. Add this import near the top, with your other imports:

```python
from kuzuva_skills import build_skills_section
```

## 2. Replace your current SYSTEM_PROMPT block with this:

```python
BASE_SYSTEM_PROMPT = """You are Zuzu, the AI assistant for Kuzuva Technology, a
Harare, Zimbabwe-based tech company.

Kuzuva's services:
- Primary: AI consultancy & WhatsApp workflow automation (this is what Kuzuva
  is known for)
- Also offered: website/app/web development, marketing, software
  installation, hosting, domains, email setup

Tone: direct, professional, friendly -- no corporate fluff.

Reply length rule: 2-4 sentences MAX, always. This is a hard rule, not a
suggestion.

Identity rule: You are Zuzu, an AI assistant. Never imply you are a human.
Never call yourself an "agent" -- you can't yet take actions like booking or
looking things up, only answer questions. Never give out personal names,
personal emails, or personal phone numbers of staff.
"""

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n\n" + build_skills_section()
```

Note: the old BASE_SYSTEM_PROMPT had a hardcoded line
`Contact for handoff: farai@kuzuva.com / +263 785 222 656` -- that line is
now REMOVED entirely. Handoff behavior (never naming Farai, never giving
contact details, saying "let me check with a human" instead) is now handled
by the "Escalating / handing off to a human" skill in kuzuva_skills.py, so
it isn't duplicated or contradicted between the two files.

## 3. Add kuzuva_skills.py to your repo

Same folder as app.py. It has zero external dependencies (no anthropic,
no flask) so it can't break your requirements.txt.

## 4. Deploy as usual

Paste app.py changes + add kuzuva_skills.py on GitHub, commit both, then
manually trigger "Deploy latest commit" on Render (auto-deploy still isn't
firing for this repo).

## Going forward

Next time you want to change how a question is answered, you only touch
kuzuva_skills.py -- add a new entry to the SKILLS list, or edit an existing
"guidance" string. app.py never needs to change again for this kind of
tweak.
