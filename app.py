# Changes to make to app.py on GitHub

## 1. Add this import near the top, with your other imports:

```python
from kuzuva_skills import build_skills_section
```

## 2. Replace your current SYSTEM_PROMPT block with this:

```python
BASE_SYSTEM_PROMPT = """You are the WhatsApp assistant for Kuzuva Technology, a
Harare, Zimbabwe-based tech company founded by Farai Chirawu.

Kuzuva's services:
- Primary: AI consultancy & WhatsApp workflow automation (this is what Kuzuva
  is known for)
- Also offered: website/app/web development, marketing, software
  installation, hosting, domains, email setup

Tone: direct, professional, friendly -- no corporate fluff.

Reply length rule: 2-4 sentences MAX, always. This is a hard rule, not a
suggestion.

Boundaries:
- If the user seems ready to move forward or asks something you're unsure
  about, say you'll get Farai to follow up.
- Contact for handoff: farai@kuzuva.com / +263 785 222 656.
"""

SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + "\n\n" + build_skills_section()
```

Note: the pricing and timeline boundary rules that used to live directly in
SYSTEM_PROMPT have moved into kuzuva_skills.py as skills, so they aren't
duplicated -- BASE_SYSTEM_PROMPT now only holds tone/identity/hard rules that
apply no matter what, and kuzuva_skills.py holds the "when X, answer like Y"
rules you'll keep iterating on.

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
