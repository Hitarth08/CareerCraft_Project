# =========================
# IMPORTS
# =========================
import streamlit as st
import json
import time
import io
import fitz
from docx import Document
# import spacy
import re
from bs4 import BeautifulSoup
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from Resume_to_Project_Final import (
    clean_text,
    extract_with_llm,
    generate_company_recommendations,
    analyze_resume_ats,
    call_llm,
    extract_json
)

# =========================
# PAGE CONFIG
# =========================
st.set_page_config(
    page_title="CareerCraft — AI Career Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# =========================
# LOCAL HELPERS
# =========================
# @st.cache_resource
# def load_nlp():
#     return spacy.load("en_core_web_sm")

# nlp = load_nlp()

def extract_text_from_pdf(file_bytes):
    pdf = fitz.open(stream=file_bytes, filetype="pdf")
    return "".join(page.get_text() for page in pdf)

def extract_text_from_docx(file_bytes_io):
    doc = Document(file_bytes_io)
    return "\n".join([p.text for p in doc.paragraphs])

def extract_name_spacy(text):
#     doc = nlp(text[:1000])
#     for ent in doc.ents:
#         if ent.label_ == "PERSON":
#             return ent.text
#     return "Not Found"
# spaCy removed — LLM handles name extraction
    return "Not Found"


# =========================
# GOOGLE SHEETS LOGGING
# =========================

# ── LOCAL VERSION (active now — reads JSON file from project folder) ──
# def get_sheet():
#     scope = [
#         "https://spreadsheets.google.com/feeds",
#         "https://www.googleapis.com/auth/drive"
#     ]
#     creds = Credentials.from_service_account_file(
#         "careercraft-credentials.json",   # put this JSON file in your project folder
#         scopes=scope
#     )
#     client = gspread.authorize(creds)
#     sheet  = client.open("CareerCraft Users").sheet1
#     return sheet

#── STREAMLIT CLOUD VERSION (uncomment this when deploying, comment out the one above) ──
def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = st.secrets["gcp_service_account"]
    creds  = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    sheet  = client.open("CareerCraft Users").sheet1
    return sheet


def log_user(profile, ats_data=None, goal=None, target_role=None):
    try:
        sheet = get_sheet()

        name       = profile.get("Name", "Unknown")
        skills     = profile.get("Skills", [])
        education  = profile.get("Education", [])
        roles      = profile.get("Role", [])
        experience = format_experience(profile.get("Work_Experience_in_Years", ""))
        projects   = profile.get("Projects", [])
        ats_score  = ats_data.get("ATS_Score", "Not Run") if ats_data and "error" not in ats_data else "Not Run"

        edu_summary = ""
        if education and isinstance(education[0], dict):
            edu_summary = education[0].get("degree", "") + " — " + education[0].get("college", "")

        import pytz
        from datetime import datetime
        ist = pytz.timezone("Asia/Kolkata")
        timestamp = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

        row = [
            timestamp,
            name,
            edu_summary,
            len(skills),
            ", ".join(skills[:5]),
            ", ".join(roles),
            experience,
            len(projects),
            ats_score,
            goal if goal else "Not Selected",
            target_role if target_role else "N/A"
        ]

        # If row already exists for this session — update it
        if st.session_state.get("log_row_number"):
            row_num = st.session_state.log_row_number
            sheet.update(f"A{row_num}:K{row_num}", [row])
        else:
            # First time — append and save row number
            sheet.append_row(row)
            log_row = len(sheet.get_all_values())
            st.session_state.log_row_number = log_row

    except Exception as e:
        print(f"Logging error: {e}")
        pass

# =========================
# PROJECT FUNCTIONS
# =========================
def generate_project_upskilling(profile):
    profile_str = json.dumps(profile)[:1000]
    prompt = f"""
    You are an expert career mentor and project advisor.

    The candidate wants to UPSKILL and improve their current profile.

    Based on the following profile, suggest EXACTLY 5 project ideas that will
    help them grow beyond what they have already done.

    Profile:
    {profile_str}

    Requirements:
    - Do NOT suggest projects similar to existing ones in the profile
    - Projects must fill skill gaps or extend current strengths
    - Must be INDUSTRY-RELEVANT (2026 level)
    - Distribute levels smartly (e.g. 2 Beginner, 2 Intermediate, 1 Expert)

    Each project must include:
    - Title
    - Level (Beginner / Intermediate / Expert)
    - Description (5-6 lines explaining what the project does)
    - Why_Suitable (why this helps the candidate upskill based on their profile)
    - Tech_Stack (list of tools/technologies)

    Return STRICT JSON only — no explanation, no markdown:

    {{
        "Goal": "Upskilling",
        "Recommended_Projects": [
            {{
                "Title": "",
                "Level": "",
                "Description": "",
                "Why_Suitable": "",
                "Tech_Stack": []
            }}
        ]
    }}
    """
    result = call_llm(prompt, max_tokens=2000)
    json_str = extract_json(result)
    if json_str:
        try:
            return json.loads(json_str)
        except:
            return {"error": "JSON parsing failed"}
    return {"error": "No JSON found"}


def generate_project_job(profile, target_companies, target_role):
    profile_str = json.dumps(profile)[:1000]
    companies_str = ", ".join(target_companies)
    prompt = f"""
    You are an expert career mentor and project advisor.

    The candidate is targeting a JOB and wants project recommendations
    that will maximize their chances of getting hired.

    Candidate Profile:
    {profile_str}

    Target Companies: {companies_str}
    Target Role: {target_role}

    Requirements:
    - Suggest EXACTLY 5 projects that directly align with what these companies
      look for in a {target_role}
    - Do NOT suggest projects similar to existing ones in the profile
    - Projects should demonstrate skills specifically valued for {target_role} at {companies_str}
    - Must be INDUSTRY-RELEVANT (2026 level)
    - Distribute levels smartly (e.g. 2 Beginner, 2 Intermediate, 1 Expert)

    Each project must include:
    - Title
    - Level (Beginner / Intermediate / Expert)
    - Description (5-6 lines explaining what the project does)
    - Why_Suitable (why this project helps get hired — be specific)
    - Tech_Stack (list of tools/technologies)

    Return STRICT JSON only — no explanation, no markdown:

    {{
        "Goal": "Job",
        "Target_Companies": {json.dumps(target_companies)},
        "Target_Role": "{target_role}",
        "Recommended_Projects": [
            {{
                "Title": "",
                "Level": "",
                "Description": "",
                "Why_Suitable": "",
                "Tech_Stack": []
            }}
        ]
    }}
    """
    result = call_llm(prompt, max_tokens=2000)
    json_str = extract_json(result)
    if json_str:
        try:
            return json.loads(json_str)
        except:
            return {"error": "JSON parsing failed"}
    return {"error": "No JSON found"}


# =========================
# PDF REPORT GENERATOR
# =========================
def generate_pdf_report(profile, companies_data, projects_data, ats_data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=0.75*inch, leftMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'],
        fontSize=28, textColor=colors.HexColor('#7c3aed'), spaceAfter=6, fontName='Helvetica-Bold')
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading1'],
        fontSize=14, textColor=colors.HexColor('#3b82f6'), spaceBefore=18, spaceAfter=8, fontName='Helvetica-Bold')
    subheading_style = ParagraphStyle('SubHeading', parent=styles['Heading2'],
        fontSize=12, textColor=colors.HexColor('#7c3aed'), spaceBefore=12, spaceAfter=6, fontName='Helvetica-Bold')
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'],
        fontSize=10, textColor=colors.HexColor('#333333'), spaceAfter=4, leading=16)
    tag_style = ParagraphStyle('Tag', parent=styles['Normal'],
        fontSize=10, textColor=colors.HexColor('#1e40af'), spaceAfter=4, leading=16)

    story = []

    story.append(Paragraph("CareerCraft", title_style))
    story.append(Paragraph("AI-Powered Career Recommendation Report", normal_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#7c3aed'), spaceAfter=16))

    story.append(Paragraph("Candidate Profile", heading_style))
    story.append(Paragraph(f"<b>Name:</b> {profile.get('Name', 'Not Found')}", normal_style))
    story.append(Paragraph(f"<b>Experience:</b> {format_experience(profile.get('Work_Experience_in_Years', ''))}", normal_style))

    roles = profile.get("Role", [])
    if roles:
        story.append(Paragraph(f"<b>Suitable Roles:</b> {' | '.join(roles)}", normal_style))

    education = profile.get("Education", [])
    if education:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Education:</b>", normal_style))
        for e in education:
            if isinstance(e, dict):
                degree  = e.get("degree", "")
                college = e.get("college", "")
                label   = degree + (f" — {college}" if college else "")
            else:
                label = str(e)
            story.append(Paragraph(f"  • {label}", normal_style))

    skills = profile.get("Skills", [])
    if skills:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Skills:</b>", normal_style))
        story.append(Paragraph("  •  ".join(skills), tag_style))

    projects_done = profile.get("Projects", [])
    if projects_done:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Past Projects:</b>", normal_style))
        for p in projects_done:
            story.append(Paragraph(f"  • {p}", normal_style))

    companies_worked = profile.get("Work_Experience_Company", [])
    if companies_worked:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>Work Experience:</b> {', '.join(companies_worked)}", normal_style))

    if ats_data and "error" not in ats_data:
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceBefore=16, spaceAfter=8))
        story.append(Paragraph("ATS Resume Analysis", heading_style))
        score = ats_data.get("ATS_Score", 0)
        score_label = "Good" if score >= 75 else "Needs Work" if score >= 50 else "Poor"
        story.append(Paragraph(f"<b>ATS Score: {score}/100 — {score_label}</b>", normal_style))
        breakdown = ats_data.get("Score_Breakdown", {})
        if breakdown:
            bd_text = "  |  ".join([f"{k.replace('_',' ')}: {v}" for k, v in breakdown.items()])
            story.append(Paragraph(f"<b>Breakdown:</b> {bd_text}", normal_style))
        quick_wins = ats_data.get("Quick_Wins", [])
        if quick_wins:
            story.append(Spacer(1, 6))
            story.append(Paragraph("<b>Quick Wins:</b>", normal_style))
            for i, win in enumerate(quick_wins, 1):
                story.append(Paragraph(f"  {i}. {win}", normal_style))
        missing_kw = ats_data.get("Missing_Keywords", [])
        if missing_kw:
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>Missing Keywords:</b> {', '.join(missing_kw)}", tag_style))
        issues = ats_data.get("Critical_Issues", [])
        if issues:
            story.append(Spacer(1, 6))
            story.append(Paragraph("<b>Critical Issues:</b>", normal_style))
            for issue in issues:
                story.append(Paragraph(f"  • {issue}", normal_style))
        projected = ats_data.get("Projected_ATS_Score_After_Changes", 0)
        if projected:
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>Projected Score After Changes: {projected}/100</b>", normal_style))

    if companies_data and "error" not in companies_data:
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceBefore=16, spaceAfter=8))
        story.append(Paragraph("Company Recommendations", heading_style))
        recs = companies_data.get("Company_Recommendations", {})
        for key, label in [("Tier_1_Dream","Tier 1 — Dream"),("Tier_2_Good_Fit","Tier 2 — Good Fit"),("Tier_3_Safe_Bet","Tier 3 — Safe Bets")]:
            tier_data = recs.get(key, [])
            if not tier_data:
                continue
            story.append(Paragraph(label, subheading_style))
            for item in tier_data:
                story.append(Paragraph(f"  <b>{item.get('Company','')}</b> — {item.get('Reason','')}", normal_style))

    if projects_data and "error" not in projects_data:
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceBefore=16, spaceAfter=8))
        goal        = projects_data.get("Goal", "")
        target_role = projects_data.get("Target_Role", "")
        target_cos  = projects_data.get("Target_Companies", [])
        heading_text = "Recommended Projects"
        if goal == "Job" and target_role:
            heading_text += f" — {target_role} at {', '.join(target_cos)}"
        story.append(Paragraph(heading_text, heading_style))
        level_colors = {"beginner": "#16a34a", "intermediate": "#d97706", "expert": "#dc2626"}
        for i, proj in enumerate(projects_data.get("Recommended_Projects", []), 1):
            level     = proj.get("Level", "")
            lvl_color = level_colors.get(level.lower(), "#7c3aed")
            story.append(Spacer(1, 8))
            story.append(Paragraph(f"{i}. {proj.get('Title','')}", subheading_style))
            story.append(Paragraph(f'<font color="{lvl_color}"><b>[{level}]</b></font>', normal_style))
            story.append(Paragraph(f"<b>Description:</b> {proj.get('Description','')}", normal_style))
            story.append(Paragraph(f"<b>Why Suitable:</b> {proj.get('Why_Suitable','')}", normal_style))
            story.append(Paragraph(f"<b>Tech Stack:</b> {', '.join(proj.get('Tech_Stack', []))}", tag_style))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e2e8f0'), spaceAfter=8))
    story.append(Paragraph("Generated by CareerCraft — AI-Powered Career Intelligence", normal_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


# =========================
# FORMAT EXPERIENCE
# =========================
def format_experience(years_raw):
    if not years_raw or str(years_raw).strip() in ["", "0", "0.0", "None", "Not Found"]:
        return "Fresher"
    raw = str(years_raw).strip().lower()
    if "month" in raw:
        try:
            months = int(''.join(filter(str.isdigit, raw)))
            return "Fresher" if months == 0 else f"{months} mo"
        except:
            return raw
    try:
        val = float(raw.replace("years", "").replace("year", "").strip())
        if val == 0:          return "Fresher"
        elif val < 1:         return f"{round(val * 12)} mo"
        elif val == int(val): return f"{int(val)} yr"
        else:                 return f"{val} yr"
    except:
        return str(years_raw)


# =========================
# CUSTOM CSS
# =========================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:wght@300;400;500&display=swap');

html, body, [data-testid="stAppViewContainer"] {
    background: #0a0a0f; color: #e8e6f0; font-family: 'DM Sans', sans-serif;
}
[data-testid="stAppViewContainer"] {
    background: radial-gradient(ellipse at 20% 0%, #1a0533 0%, transparent 50%),
                radial-gradient(ellipse at 80% 10%, #001a33 0%, transparent 50%), #0a0a0f;
}
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stSidebar"] { display: none; }
.block-container { padding: 2rem 4rem !important; max-width: 1300px !important; margin: 0 auto !important; }

.app-header { text-align: center; padding: 48px 20px 36px; }
.app-badge {
    display: inline-block;
    background: linear-gradient(135deg, rgba(139,92,246,0.2), rgba(59,130,246,0.2));
    border: 1px solid rgba(139,92,246,0.4); color: #a78bfa; font-size: 11px; font-weight: 500;
    letter-spacing: 2px; text-transform: uppercase; padding: 5px 16px; border-radius: 20px; margin-bottom: 20px;
}
.app-title {
    font-family: 'Syne', sans-serif; font-size: clamp(40px, 5vw, 68px); font-weight: 800;
    line-height: 1.05; letter-spacing: -2px;
    background: linear-gradient(135deg, #ffffff 0%, #a78bfa 50%, #60a5fa 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; margin-bottom: 14px;
}
.app-sub { font-size: 20px; color: #64748b; max-width: 480px; margin: 0 auto; line-height: 1.7; }
.divider {
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(139,92,246,0.3), rgba(59,130,246,0.3), transparent);
    margin: 32px 0 40px;
}
.card { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; padding: 24px; margin-bottom: 16px; }
.card-title { font-family: 'Syne', sans-serif; font-size: 15px; font-weight: 600; letter-spacing: 2px; text-transform: uppercase; color: #64748b; margin-bottom: 12px; }

.tag { display: inline-block; padding: 4px 12px; margin: 3px; border-radius: 6px; font-size: 15px; font-weight: 500; }
.tag-skill   { background: rgba(96,165,250,0.12);  border: 1px solid rgba(96,165,250,0.25);  color: #93c5fd; }
.tag-project { background: rgba(52,211,153,0.10);  border: 1px solid rgba(52,211,153,0.25);  color: #6ee7b7; }
.tag-company { background: rgba(251,191,36,0.10);  border: 1px solid rgba(251,191,36,0.25);  color: #fcd34d; }
.tag-role    { background: rgba(139,92,246,0.12);  border: 1px solid rgba(139,92,246,0.30);  color: #c4b5fd; }
.tag-edu     { background: rgba(244,114,182,0.10); border: 1px solid rgba(244,114,182,0.25); color: #f9a8d4; }
.tag-keyword { background: rgba(96,165,250,0.12);  border: 1px solid rgba(96,165,250,0.25);  color: #93c5fd; }

.profile-name {
    font-family: 'Syne', sans-serif; font-size: 38px; font-weight: 800;
    background: linear-gradient(135deg, #ffffff, #a78bfa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; margin-bottom: 4px;
}
.stat-row { display: grid; grid-template-columns: repeat(3,1fr); gap: 14px; margin: 20px 0; }
.stat-box { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.07); border-radius: 14px; padding: 18px; text-align: center; }
.stat-num { font-family: 'Syne',sans-serif; font-size: 30px; font-weight: 800; background: linear-gradient(135deg,#a78bfa,#60a5fa); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; }
.stat-lbl { font-size: 11px; color: #64748b; margin-top: 4px; text-transform: uppercase; letter-spacing: 1px; }

.sec-heading { font-family: 'Syne', sans-serif; font-size: 26px; font-weight: 700; color: #f1f0f7; margin: 36px 0 20px; display: flex; align-items: center; gap: 10px; }

.tier-label { font-size: 11px; font-weight: 600; letter-spacing: 1.5px; text-transform: uppercase; padding: 4px 12px; border-radius: 20px; display: inline-block; margin-bottom: 14px; }
.tier-1 { background: rgba(251,191,36,0.15); border: 1px solid rgba(251,191,36,0.35); color: #fcd34d; }
.tier-2 { background: rgba(139,92,246,0.15); border: 1px solid rgba(139,92,246,0.35); color: #c4b5fd; }
.tier-3 { background: rgba(52,211,153,0.15); border: 1px solid rgba(52,211,153,0.35); color: #6ee7b7; }

.company-row { display: flex; align-items: flex-start; gap: 14px; background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 16px 18px; margin-bottom: 10px; }
.company-icon { width: 38px; height: 38px; border-radius: 9px; background: rgba(139,92,246,0.12); display: flex; align-items: center; justify-content: center; font-size: 17px; flex-shrink: 0; }
.company-name { font-family: 'Syne',sans-serif; font-size: 18px; font-weight: 600; color: #f1f0f7; margin-bottom: 3px; }
.company-reason { font-size: 15px; color: #64748b; line-height: 1.5; }

.proj-card { background: linear-gradient(135deg,rgba(255,255,255,0.04),rgba(255,255,255,0.01)); border: 1px solid rgba(255,255,255,0.08); border-radius: 18px; padding: 26px; margin-bottom: 18px; position: relative; overflow: hidden; }
.proj-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, #7c3aed, #3b82f6); }
.proj-title { font-family: 'Syne',sans-serif; font-size: 23px; font-weight: 700; color: #f1f0f7; margin-bottom: 8px; }
.proj-level { display: inline-block; font-size: 13px; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; padding: 3px 9px; border-radius: 4px; margin-bottom: 12px; }
.lvl-b { background:rgba(52,211,153,0.15); color:#6ee7b7; border:1px solid rgba(52,211,153,0.3); }
.lvl-i { background:rgba(251,191,36,0.15); color:#fcd34d; border:1px solid rgba(251,191,36,0.3); }
.lvl-e { background:rgba(239,68,68,0.15); color:#fca5a5; border:1px solid rgba(239,68,68,0.3); }
.proj-desc { font-size: 16px; color: #94a3b8; line-height: 1.7; margin-bottom: 14px; }
.proj-why  { background: rgba(139,92,246,0.08); border-left: 3px solid #7c3aed; border-radius: 0 8px 8px 0; padding: 10px 14px; font-size: 15px; color: #c4b5fd; line-height: 1.6; margin-bottom: 14px; }
.tech-wrap { display:flex; flex-wrap:wrap; gap:5px; }
.tech-pill { background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.2); color:#93c5fd; font-size:15px; font-weight:500; padding:3px 9px; border-radius:4px; }

.goal-card { border-radius: 16px; padding: 28px; text-align: center; cursor: pointer; margin-bottom: 12px; }
.goal-card-upskill { background:rgba(139,92,246,0.06); border:1.5px solid rgba(139,92,246,0.22); }
.goal-card-job     { background:rgba(59,130,246,0.06);  border:1.5px solid rgba(59,130,246,0.22); }
.goal-icon  { font-size: 34px; margin-bottom: 10px; }
.goal-title { font-family:'Syne',sans-serif; font-size:17px; font-weight:700; color:#f1f0f7; margin-bottom:6px; }
.goal-desc  { font-size:12px; color:#64748b; }

.banner { border-radius: 10px; padding: 14px 18px; font-size: 13px; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; }
.banner-success { background:rgba(52,211,153,0.08); border:1px solid rgba(52,211,153,0.22); color:#6ee7b7; }
.banner-info    { background:rgba(59,130,246,0.08);  border:1px solid rgba(59,130,246,0.22);  color:#93c5fd; }

[data-testid="stFileUploader"] > div {
    background: rgba(139,92,246,0.05) !important;
    border: 1.5px dashed rgba(139,92,246,0.35) !important;
    border-radius: 14px !important;
}
.stButton > button {
    background: linear-gradient(135deg,#7c3aed,#3b82f6) !important;
    color: white !important; border: none !important; border-radius: 10px !important;
    font-family: 'DM Sans',sans-serif !important; font-weight: 500 !important;
    font-size: 14px !important; padding: 11px 28px !important; width: 100% !important;
}
.stButton > button:hover { opacity: 0.88 !important; }
.stTextInput > div > div > input {
    background: rgba(255,255,255,0.04) !important; border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 10px !important; color: #e8e6f0 !important;
    font-family: 'DM Sans',sans-serif !important; padding: 11px 14px !important;
}
.stTextInput > div > div > input:focus {
    border-color: rgba(139,92,246,0.5) !important; box-shadow: 0 0 0 2px rgba(139,92,246,0.12) !important;
}
.stTextInput label { color: #94a3b8 !important; font-size: 12px !important; }
[data-testid="stProgress"] > div > div { background: linear-gradient(90deg,#7c3aed,#3b82f6) !important; border-radius: 99px !important; }
</style>
""", unsafe_allow_html=True)


# =========================
# SESSION STATE
# =========================
defaults = {
    "profile":          None,
    "companies":        None,
    "projects":         None,
    "goal":             None,
    "target_companies": None,
    "target_role":      None,
    "show_companies":   False,
    "show_projects":    False,
    "ats_data":         None,
    "show_ats":         False,
    "raw_text":         None,
    "logged":           False,
    "log_row_number":   None, 
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =========================
# HEADER
# =========================
st.markdown("""
<div class="app-header">
    <div class="app-badge">⚡ AI-Powered Career Recommendation System</div>
    <div class="app-title">CareerCraft</div>
    <div class="app-sub">Upload your resume and get an instant profile breakdown,
    company targets, ATS analysis, and tailored project ideas.</div>
</div>
<div class="divider"></div>
""", unsafe_allow_html=True)


# =========================
# UPLOAD
# =========================
uploaded_file = st.file_uploader(
    "Upload Resume (PDF or DOCX)",
    type=["pdf", "docx"],
    label_visibility="collapsed"
)


# =========================
# MAIN FLOW
# =========================
if uploaded_file is not None:

    current_file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    if st.session_state.get("current_file_id") != current_file_id:
        for k, v in defaults.items():
            st.session_state[k] = v
        st.session_state["current_file_id"] = current_file_id

    if st.session_state.profile is None:

        file_bytes = uploaded_file.read()
        progress   = st.progress(0)
        status     = st.empty()
        steps      = ["📄 Reading resume...","🧹 Cleaning text...","🧠 Extracting with AI...","📊 Structuring profile..."]

        for i, step in enumerate(steps):
            status.markdown(f"<span style='color:#94a3b8;font-size:13px'>{step}</span>", unsafe_allow_html=True)
            progress.progress((i + 1) * 20)
            time.sleep(0.4)

        if uploaded_file.name.endswith(".pdf"):
            raw_text = extract_text_from_pdf(file_bytes)
        else:
            raw_text = extract_text_from_docx(io.BytesIO(file_bytes))

        cleaned = clean_text(raw_text)
        status.markdown("<span style='color:#94a3b8;font-size:13px'>🤖 Running AI extraction...</span>", unsafe_allow_html=True)
        progress.progress(80)

        profile = extract_with_llm(cleaned)
        if profile.get("Name") in ["", "Not Found", None]:
            profile["Name"] = extract_name_spacy(cleaned)

        progress.progress(100)
        time.sleep(0.3)
        progress.empty()
        status.empty()

        st.session_state.profile  = profile
        st.session_state.raw_text = cleaned

        # Log user to Google Sheets (runs once per resume upload)
        log_user(
            profile
            # st.session_state.ats_data,
            # st.session_state.goal,
            # st.session_state.target_role
        )
        st.session_state.logged = True

    profile = st.session_state.profile

    st.markdown(f"""
    <div class="banner banner-success">
        ✅ &nbsp; <strong>{uploaded_file.name}</strong> analysed successfully
    </div>""", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # SECTION 1 — PROFILE
    # ════════════════════════════════════════════════════════════
    st.markdown('<div class="sec-heading">📊 Extracted Profile</div>', unsafe_allow_html=True)

    name          = profile.get("Name", "Not Found")
    skills        = profile.get("Skills", [])
    projects_done = profile.get("Projects", [])
    years         = profile.get("Work_Experience_in_Years", "")
    exp_display   = format_experience(years)

    st.markdown(f"""
    <div class="card">
        <div class="profile-name">{name}</div>
        <div style="color:#64748b;font-size:12px;margin-bottom:14px;">Candidate</div>
        <div class="stat-row">
            <div class="stat-box"><div class="stat-num">{len(skills)}</div><div class="stat-lbl">Skills</div></div>
            <div class="stat-box"><div class="stat-num">{len(projects_done)}</div><div class="stat-lbl">Projects</div></div>
            <div class="stat-box"><div class="stat-num">{exp_display}</div><div class="stat-lbl">Experience</div></div>
        </div>
    </div>""", unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        roles = profile.get("Role", [])
        if roles:
            roles_html = "".join([f'<span class="tag tag-role">{r}</span>' for r in roles])
            st.markdown(f'<div class="card"><div class="card-title">🎯 Suitable Roles</div><div>{roles_html}</div></div>', unsafe_allow_html=True)

        education = profile.get("Education", [])
        if education:
            edu_tags = ""
            for e in education:
                if isinstance(e, dict):
                    degree  = e.get("degree", "")
                    college = e.get("college", "")
                    label   = degree + (f" — {college}" if college else "")
                else:
                    label = str(e)
                edu_tags += f'<span class="tag tag-edu">{label}</span>'
            st.markdown(f'<div class="card"><div class="card-title">🎓 Education</div><div>{edu_tags}</div></div>', unsafe_allow_html=True)

        companies_worked = profile.get("Work_Experience_Company", [])
        if companies_worked:
            comp_tags = "".join([f'<span class="tag tag-company">🏢 {c}</span>' for c in companies_worked])
            st.markdown(f'<div class="card"><div class="card-title">💼 Work Experience</div><div>{comp_tags}</div></div>', unsafe_allow_html=True)

    with col2:
        if skills:
            skill_tags = "".join([f'<span class="tag tag-skill">{s}</span>' for s in skills])
            st.markdown(f'<div class="card"><div class="card-title">⚡ Skills</div><div>{skill_tags}</div></div>', unsafe_allow_html=True)
        if projects_done:
            proj_tags = "".join([f'<span class="tag tag-project">▸ {p}</span>' for p in projects_done])
            st.markdown(f'<div class="card"><div class="card-title">🛠 Past Projects</div><div>{proj_tags}</div></div>', unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # SECTION 2 — ATS RESUME ANALYZER
    # ════════════════════════════════════════════════════════════
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec-heading">🎯 ATS Resume Analyzer</div>', unsafe_allow_html=True)

    if not st.session_state.show_ats:
        if st.button("Analyze Resume for ATS"):
            st.session_state.show_ats = True
            st.rerun()

    if st.session_state.show_ats:
        if st.session_state.ats_data is None:
            prog_a  = st.progress(0)
            stat_a  = st.empty()
            steps_a = ["📄 Reading resume structure...","🔍 Checking keyword density...","⚙️ Analysing ATS compatibility...","📊 Scoring your resume..."]
            for i, step in enumerate(steps_a):
                stat_a.markdown(f"<span style='color:#94a3b8;font-size:13px'>{step}</span>", unsafe_allow_html=True)
                prog_a.progress((i + 1) * 25)
                time.sleep(0.45)
            st.session_state.ats_data = analyze_resume_ats(st.session_state.raw_text, st.session_state.profile)
            prog_a.empty()
            stat_a.empty()
            log_user(
                st.session_state.profile,
                ats_data=st.session_state.ats_data
            )

        ats = st.session_state.ats_data

        if "error" in ats:
            st.error(f"Could not analyze: {ats['error']}")
        else:
            score       = ats.get("ATS_Score", 0)
            score_color = "#16a34a" if score >= 75 else "#d97706" if score >= 50 else "#dc2626"
            score_label = "Good"    if score >= 75 else "Needs Work" if score >= 50 else "Poor"

            st.markdown(f"""
            <div class="card" style="text-align:center; padding:32px;">
                <div style="font-family:'Syne',sans-serif; font-size:13px; color:#64748b;
                            letter-spacing:2px; text-transform:uppercase; margin-bottom:12px;">
                    ATS Compatibility Score
                </div>
                <div style="font-family:'Syne',sans-serif; font-size:72px; font-weight:800;
                            color:{score_color}; line-height:1;">{score}</div>
                <div style="font-size:14px; color:{score_color}; margin-top:8px; font-weight:600;">
                    {score_label}
                </div>
            </div>""", unsafe_allow_html=True)

            breakdown = ats.get("Score_Breakdown", {})
            if breakdown:
                st.markdown('<div class="card"><div class="card-title">📊 Score Breakdown</div>', unsafe_allow_html=True)
                cols = st.columns(len(breakdown))
                for col, (key, val) in zip(cols, breakdown.items()):
                    with col:
                        bc = "#16a34a" if val >= 75 else "#d97706" if val >= 50 else "#dc2626"
                        st.markdown(f"""
                        <div style="text-align:center; padding:12px; background:rgba(255,255,255,0.02);
                                    border:1px solid rgba(255,255,255,0.06); border-radius:10px;">
                            <div style="font-family:'Syne',sans-serif; font-size:22px; font-weight:800; color:{bc};">{val}</div>
                            <div style="font-size:11px; color:#64748b; margin-top:4px;">{key.replace('_',' ')}</div>
                        </div>""", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

            col_l, col_r = st.columns(2)
            with col_l:
                issues = ats.get("Critical_Issues", [])
                if issues:
                    st.markdown('<div class="card"><div class="card-title" style="color:#ef4444;">🚨 Critical Issues</div>', unsafe_allow_html=True)
                    for issue in issues:
                        st.markdown(f"""
                        <div style="display:flex; gap:10px; align-items:flex-start; padding:10px 0;
                                    border-bottom:1px solid rgba(255,255,255,0.04);">
                            <span style="color:#ef4444; font-size:16px; flex-shrink:0;">✗</span>
                            <span style="font-size:13px; color:#94a3b8; line-height:1.5;">{issue}</span>
                        </div>""", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

            with col_r:
                strong = ats.get("Strong_Points", [])
                if strong:
                    st.markdown('<div class="card"><div class="card-title" style="color:#16a34a;">💪 Strong Points</div>', unsafe_allow_html=True)
                    for point in strong:
                        st.markdown(f"""
                        <div style="display:flex; gap:10px; align-items:flex-start; padding:10px 0;
                                    border-bottom:1px solid rgba(255,255,255,0.04);">
                            <span style="color:#16a34a; font-size:16px; flex-shrink:0;">✓</span>
                            <span style="font-size:13px; color:#94a3b8; line-height:1.5;">{point}</span>
                        </div>""", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

            quick_wins = ats.get("Quick_Wins", [])
            if quick_wins:
                st.markdown('<div class="card"><div class="card-title">⚡ Quick Wins — Do These First</div>', unsafe_allow_html=True)
                for i, win in enumerate(quick_wins):
                    st.markdown(f"""
                    <div style="display:flex; gap:12px; align-items:flex-start; padding:12px;
                                background:rgba(139,92,246,0.05); border:1px solid rgba(139,92,246,0.15);
                                border-radius:10px; margin-bottom:8px;">
                        <span style="background:linear-gradient(135deg,#7c3aed,#3b82f6); color:white;
                                     font-size:11px; font-weight:700; padding:2px 8px;
                                     border-radius:4px; flex-shrink:0;">#{i+1}</span>
                        <span style="font-size:13px; color:#c4b5fd; line-height:1.5;">{win}</span>
                    </div>""", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

            keywords = ats.get("Missing_Keywords", [])
            if keywords:
                kw_html = "".join([f'<span class="tag tag-keyword">{k}</span>' for k in keywords])
                st.markdown(f'<div class="card"><div class="card-title">🔑 Missing Keywords — Add These to Your Resume</div><div>{kw_html}</div></div>', unsafe_allow_html=True)

            fmt_issues = ats.get("Formatting_Issues", [])
            if fmt_issues:
                st.markdown('<div class="card"><div class="card-title" style="color:#f59e0b;">⚠️ Formatting Issues</div>', unsafe_allow_html=True)
                for issue in fmt_issues:
                    st.markdown(f"""
                    <div style="display:flex; gap:10px; padding:8px 0;
                                border-bottom:1px solid rgba(255,255,255,0.04);">
                        <span style="color:#f59e0b; flex-shrink:0;">⚠</span>
                        <span style="font-size:13px; color:#94a3b8;">{issue}</span>
                    </div>""", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)

            section_fb = ats.get("Section_Feedback", {})
            icons_map  = {"Summary":"📝","Skills":"⚡","Experience":"💼","Projects":"🛠","Education":"🎓"}
            status_cfg = {
                "Strong":            ("#16a34a", "rgba(52,211,153,0.08)",  "rgba(52,211,153,0.25)"),
                "Needs Improvement": ("#d97706", "rgba(251,191,36,0.08)",  "rgba(251,191,36,0.25)"),
                "Missing":           ("#dc2626", "rgba(239,68,68,0.08)",   "rgba(239,68,68,0.25)"),
            }

            if section_fb:
                st.markdown('<div class="card"><div class="card-title">📋 Section-by-Section Feedback</div>', unsafe_allow_html=True)
                for section, data in section_fb.items():
                    if not data:
                        continue
                    icon = icons_map.get(section, "•")

                    if isinstance(data, dict):

                        status = str(data.get("Status", "")).strip()

                        # CLEAN FEEDBACK
                        feedback_raw = str(data.get("Feedback", ""))

                        feedback = BeautifulSoup(feedback_raw, "html.parser").get_text(" ")

                        feedback = re.sub(r'\s+', ' ', feedback).strip()

                        # CLEAN SUGGESTION
                        suggestion_raw = str(data.get("Suggestion", ""))

                        suggestion = BeautifulSoup(suggestion_raw, "html.parser").get_text(" ")

                        suggestion = re.sub(r'\s+', ' ', suggestion).strip()

                    else:

                        status = ""

                        feedback = BeautifulSoup(str(data), "html.parser").get_text(" ")

                        feedback = re.sub(r'\s+', ' ', feedback).strip()

                        suggestion = ""
                    txt_color, bg_color, border_color = status_cfg.get(
                        status, ("#94a3b8", "rgba(255,255,255,0.03)", "rgba(255,255,255,0.08)")
                    )

                    badge_html = f"""
                    <span style="font-size:10px; font-weight:600; letter-spacing:1px;
                                 text-transform:uppercase; padding:2px 10px; border-radius:20px;
                                 color:{txt_color}; background:{bg_color};
                                 border:1px solid {border_color};">{status}</span>
                    """ if status else ""

                    suggestion_html = f"""
                    <div style="font-size:12px; color:#a78bfa; line-height:1.5; padding:8px 12px;
                                background:rgba(139,92,246,0.08); border-left:2px solid #7c3aed;
                                border-radius:0 6px 6px 0; margin-top:8px;">
                        💡 {suggestion}
                    </div>""" if suggestion else ""
                    
                    with st.container():

                        st.markdown(f"""
                        <div style="
                            padding:16px;
                            margin-bottom:10px;
                            background:{bg_color};
                            border:1px solid {border_color};
                            border-radius:12px;
                        ">
                        """, unsafe_allow_html=True)

                        col1, col2 = st.columns([8,2])

                        with col1:
                            st.markdown(
                                f"**{icon} {section}**",
                                unsafe_allow_html=False
                            )

                        with col2:
                            st.markdown(
                                badge_html,
                                unsafe_allow_html=True
                            )

                        st.write(feedback)

                        if suggestion:
                            st.info(suggestion)

                        st.markdown("</div>", unsafe_allow_html=True)

            projected = ats.get("Projected_ATS_Score_After_Changes", 0)
            if projected:
                improvement = projected - score
                imp_color   = "#16a34a" if improvement > 0 else "#64748b"
                st.markdown(f"""
                <div class="card" style="text-align:center; padding:28px;
                            background:rgba(139,92,246,0.05); border:1px solid rgba(139,92,246,0.2);">
                    <div style="font-size:12px; color:#64748b; letter-spacing:2px;
                                text-transform:uppercase; margin-bottom:16px;">
                        Projected ATS Score After All Changes
                    </div>
                    <div style="display:flex; align-items:center; justify-content:center; gap:24px;">
                        <div>
                            <div style="font-family:'Syne',sans-serif; font-size:28px;
                                        font-weight:700; color:#64748b;">{score}</div>
                            <div style="font-size:11px; color:#64748b; margin-top:4px;">Current</div>
                        </div>
                        <div style="font-size:28px; color:#a78bfa;">→</div>
                        <div>
                            <div style="font-family:'Syne',sans-serif; font-size:52px;
                                        font-weight:800; color:#a78bfa; line-height:1;">{projected}</div>
                            <div style="font-size:11px; color:#64748b; margin-top:4px;">After Changes</div>
                        </div>
                        <div style="font-size:20px; font-weight:700; color:{imp_color};">
                            +{improvement} ↑
                        </div>
                    </div>
                </div>""", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # SECTION 3 — COMPANY RECOMMENDATIONS
    # ════════════════════════════════════════════════════════════
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec-heading">🏢 Company Recommendations</div>', unsafe_allow_html=True)

    if not st.session_state.show_companies:
        if st.button("Generate Company Recommendations"):
            st.session_state.show_companies = True
            st.rerun()

    if st.session_state.show_companies:
        if st.session_state.companies is None:
            prog_c  = st.progress(0)
            stat_c  = st.empty()
            steps_c = ["🔍 Reading profile...","📊 Matching market...","🏢 Ranking companies...","✅ Done!"]
            for i, step in enumerate(steps_c):
                stat_c.markdown(f"<span style='color:#94a3b8;font-size:13px'>{step}</span>", unsafe_allow_html=True)
                prog_c.progress((i + 1) * 25)
                time.sleep(0.45)
            st.session_state.companies = generate_company_recommendations(profile)
            prog_c.empty()
            stat_c.empty()

        companies_data = st.session_state.companies
        if "error" in companies_data:
            st.error(f"Could not generate: {companies_data['error']}")
        else:
            recs  = companies_data.get("Company_Recommendations", {})
            tiers = [
                ("Tier_1_Dream",    "tier-1", "🏆", "Tier 1 — Dream Companies"),
                ("Tier_2_Good_Fit", "tier-2", "⭐", "Tier 2 — Strong Match"),
                ("Tier_3_Safe_Bet", "tier-3", "✅", "Tier 3 — Safe Bets"),
            ]
            icons = ["🔵","🟣","🟢","🔴","🟡","⚪"]
            for key, badge, icon, label in tiers:
                tier_data = recs.get(key, [])
                if not tier_data:
                    continue
                st.markdown(f'<div class="tier-label {badge}">{icon} {label}</div>', unsafe_allow_html=True)
                for i, item in enumerate(tier_data):
                    st.markdown(f"""
                    <div class="company-row">
                        <div class="company-icon">{icons[i % len(icons)]}</div>
                        <div>
                            <div class="company-name">{item.get("Company","")}</div>
                            <div class="company-reason">{item.get("Reason","")}</div>
                        </div>
                    </div>""", unsafe_allow_html=True)
                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════
    # SECTION 4 — PROJECT RECOMMENDATIONS
    # ════════════════════════════════════════════════════════════
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec-heading">🚀 Project Recommendations</div>', unsafe_allow_html=True)

    if not st.session_state.show_projects:
        if st.button("Generate Project Recommendations"):
            st.session_state.show_projects = True
            st.rerun()

    if st.session_state.show_projects:

        if st.session_state.goal is None:
            st.markdown('<div style="color:#94a3b8;font-size:14px;margin-bottom:20px;">Choose your goal — we\'ll tailor 5 projects just for you.</div>', unsafe_allow_html=True)
            gc1, gc2 = st.columns(2)
            with gc1:
                st.markdown("""
                <div class="goal-card goal-card-upskill">
                    <div class="goal-icon">📈</div>
                    <div class="goal-title">Upskilling</div>
                    <div class="goal-desc">Projects that grow your skillset and strengthen your overall profile</div>
                </div>""", unsafe_allow_html=True)
                if st.button("Choose Upskilling", key="up"):
                    st.session_state.goal = "upskilling"
                    st.rerun()
            with gc2:
                st.markdown("""
                <div class="goal-card goal-card-job">
                    <div class="goal-icon">🎯</div>
                    <div class="goal-title">Job Targeting</div>
                    <div class="goal-desc">Projects aligned with specific companies and the role you're going for</div>
                </div>""", unsafe_allow_html=True)
                if st.button("Choose Job Targeting", key="job"):
                    st.session_state.goal = "job"
                    st.rerun()

        elif st.session_state.goal == "job" and st.session_state.target_role is None:
            st.markdown('<div class="banner banner-info">🎯 &nbsp; Tell us where you want to work and the role you\'re targeting</div>', unsafe_allow_html=True)
            c1   = st.text_input("Target Company 1", placeholder="e.g. Google")
            c2   = st.text_input("Target Company 2", placeholder="e.g. Microsoft")
            c3   = st.text_input("Target Company 3", placeholder="e.g. Infosys")
            role = st.text_input("Target Role",      placeholder="e.g. Data Scientist, ML Engineer")
            if st.button("Generate Projects →"):
                if c1 and c2 and c3 and role:
                    st.session_state.target_companies = [c1, c2, c3]
                    st.session_state.target_role      = role
                    st.rerun()
                else:
                    st.warning("Please fill in all 3 companies and your target role.")

        elif st.session_state.projects is None:
            prog_p  = st.progress(0)
            stat_p  = st.empty()
            steps_p = ["🧠 Understanding profile...","📊 Matching trends...","⚙️ Building projects...","🚀 Finalising..."]
            for i, step in enumerate(steps_p):
                stat_p.markdown(f"<span style='color:#94a3b8;font-size:13px'>{step}</span>", unsafe_allow_html=True)
                prog_p.progress((i + 1) * 25)
                time.sleep(0.5)
            if st.session_state.goal == "upskilling":
                st.session_state.projects = generate_project_upskilling(profile)
            else:
                st.session_state.projects = generate_project_job(
                    profile, st.session_state.target_companies, st.session_state.target_role
                )
            prog_p.empty()
            stat_p.empty()
            log_user(
                st.session_state.profile,
                ats_data=st.session_state.ats_data,
                goal=st.session_state.goal,
                target_role=st.session_state.target_role
            )
            st.rerun()


        else:
            projects_data = st.session_state.projects
            if "error" in projects_data:
                st.error(f"Could not generate projects: {projects_data['error']}")
            else:
                goal        = projects_data.get("Goal", "")
                target_role = projects_data.get("Target_Role", "")
                target_cos  = projects_data.get("Target_Companies", [])
                if goal == "Job" and target_role:
                    st.markdown(f'<div class="banner banner-info">🎯 &nbsp; Tailored for <strong>{target_role}</strong> at <strong>{", ".join(target_cos)}</strong></div>', unsafe_allow_html=True)

                level_map = {"beginner":"lvl-b","intermediate":"lvl-i","expert":"lvl-e"}
                for proj in projects_data.get("Recommended_Projects", []):
                    level      = proj.get("Level", "Beginner")
                    lvl_class  = level_map.get(level.lower(), "lvl-b")
                    tech_pills = "".join([f'<span class="tech-pill">{t}</span>' for t in proj.get("Tech_Stack", [])])
                    st.markdown(f"""
                    <div class="proj-card">
                        <div class="proj-title">{proj.get("Title","")}</div>
                        <span class="proj-level {lvl_class}">{level}</span>
                        <div class="proj-desc">{proj.get("Description","")}</div>
                        <div class="proj-why">💡 {proj.get("Why_Suitable","")}</div>
                        <div class="tech-wrap">{tech_pills}</div>
                    </div>""", unsafe_allow_html=True)

                st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
                if st.button("🔁 Try a Different Goal"):
                    st.session_state.goal             = None
                    st.session_state.projects         = None
                    st.session_state.target_companies = None
                    st.session_state.target_role      = None
                    st.rerun()

    # ════════════════════════════════════════════════════════════
    # SECTION 5 — PDF DOWNLOAD
    # ════════════════════════════════════════════════════════════
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    st.markdown('<div class="sec-heading">📄 Download Report</div>', unsafe_allow_html=True)

    if st.session_state.profile is not None:
        pdf_buffer     = generate_pdf_report(
            st.session_state.profile,
            st.session_state.companies,
            st.session_state.projects,
            st.session_state.ats_data
        )
        candidate_name = st.session_state.profile.get("Name", "Candidate").replace(" ", "_")
        st.download_button(
            label="⬇️  Download Full Report as PDF",
            data=pdf_buffer,
            file_name=f"CareerCraft_{candidate_name}_Report.pdf",
            mime="application/pdf"
        )

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    if st.button("🔄  Analyze Another Resume"):
        for k, v in defaults.items():
            st.session_state[k] = v
        st.rerun()