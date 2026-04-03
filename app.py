import os

from flask import Flask, render_template, request, redirect, session, jsonify
import mysql.connector
import requests
import json
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from skill_recommender import recommend_skills, JOB_SKILL_MAP, placement_probability, generate_roadmap
from placement_prediction import calculate_readiness_score
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-in-production")

# ── EMAIL CONFIG ──────────────────────────────────────────────────────────────
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

def send_email(to_addr, subject, html_body):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_USER
        msg["To"]      = to_addr
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_USER, to_addr, msg.as_string())
    except Exception as e:
        print("EMAIL ERROR:", e)

def send_welcome_email(to_addr, username, domain):
    send_email(to_addr, "Welcome to Career Compass!", f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;
                background:#080b18;color:#e2e8f0;border-radius:16px;">
      <h2 style="color:#a5b4fc;">◈ Career Compass</h2>
      <h3>Welcome, {username}!</h3>
      <p>Your account is ready for the <strong style="color:#22d3ee;">{domain}</strong> domain.</p>
      <p>Check your readiness, find skill gaps, and get a personalised roadmap.</p>
      <a href="http://localhost:5000" style="display:inline-block;margin-top:16px;
         padding:12px 24px;background:#6366f1;color:white;border-radius:8px;
         text-decoration:none;">Open Career Compass →</a>
    </div>""")

def send_reset_email(to_addr, token):
    url = f"http://localhost:5000/reset-password/{token}"
    send_email(to_addr, "Reset Your Password — Career Compass", f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto;padding:32px;
                background:#080b18;color:#e2e8f0;border-radius:16px;">
      <h2 style="color:#a5b4fc;">◈ Career Compass</h2>
      <h3>Password Reset</h3>
      <p>Click below to reset your password. This link expires in 1 hour.</p>
      <a href="{url}" style="display:inline-block;margin-top:16px;
         padding:12px 24px;background:#6366f1;color:white;border-radius:8px;
         text-decoration:none;">Reset Password →</a>
      <p style="margin-top:24px;font-size:12px;color:#7c87a6;">
        Didn't request this? You can ignore this email.</p>
    </div>""")


# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        # DB host, user, password, name all read from environment variables
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASS", "asdzxc@12345"),
        database=os.getenv("DB_NAME", "career_compass")
    )


# ── AI ────────────────────────────────────────────────────────────────────────
def call_ai(prompt, max_tokens=300):
    try:
        r = requests.post(
            "http://127.0.0.1:11434/api/generate",
            json={"model": "gemma3:4b", "prompt": prompt,
                  "stream": False, "options": {"num_predict": max_tokens}},
            timeout=45
        )
        if r.status_code == 200:
            out = r.json().get("response", "").strip()
            if out:
                return out
    except Exception:
        pass
    return None


# ── GAMIFICATION HELPERS ──────────────────────────────────────────────────────
LEVEL_THRESHOLDS = [
    (0,    "Newcomer",    "⚪"),
    (50,   "Beginner",   "🟤"),
    (120,  "Explorer",   "🟢"),
    (250,  "Practitioner","🔵"),
    (450,  "Achiever",   "🟣"),
    (700,  "Expert",     "🟠"),
    (1000, "Elite",      "🔴"),
    (1400, "Legend",     "🌟"),
]

def compute_level(total_assessments, best_score):
    xp = int((total_assessments * 10) + (best_score or 0))
    level = 1; name = LEVEL_THRESHOLDS[0][1]; icon = LEVEL_THRESHOLDS[0][2]
    for i, (req, lname, licon) in enumerate(LEVEL_THRESHOLDS):
        if xp >= req:
            level = i + 1; name = lname; icon = licon
    next_xp = LEVEL_THRESHOLDS[min(level, len(LEVEL_THRESHOLDS)-1)][0] if level < len(LEVEL_THRESHOLDS) else 9999
    return level, name, icon, xp, next_xp

def compute_streak(user_id):
    try:
        from datetime import date, timedelta
        db = get_db(); c = db.cursor(dictionary=True)
        c.execute(
            "SELECT DATE(taken_at) d FROM assessment_results "
            "WHERE user_id=%s GROUP BY DATE(taken_at) ORDER BY d DESC LIMIT 30",
            (user_id,)
        )
        rows = c.fetchall(); c.close(); db.close()
        if not rows: return 0
        today = date.today(); streak = 0; expected = today
        for row in rows:
            d = row["d"] if isinstance(row["d"], date) else row["d"].date()
            if d == expected or d == expected - timedelta(days=1):
                streak += 1; expected = d - timedelta(days=1)
            else:
                break
        return streak
    except Exception:
        return 0


# ── SMART FEEDBACK ────────────────────────────────────────────────────────────
def generate_smart_feedback(score, core_skills, dsa, projects, internship,
                            consistency, cgpa="", hackathons="no",
                            open_source="no", certifications="no",
                            linkedin="no", aptitude="average"):
    core_score = (len(core_skills) / 4) * 20
    dsa_score  = (len(dsa) / 6) * 24
    strengths, gaps, risks, future, actions = [], [], [], [], []

    insights = ["Your score is influenced by:"]
    if core_score:           insights.append(f"  Core subjects → ~{int(core_score)} pts")
    if dsa_score:            insights.append(f"  DSA → ~{int(dsa_score)} pts")
    if projects == "yes":    insights.append("  Projects → practical exposure")
    if internship == "yes":  insights.append("  Internship → industry advantage")
    cs = {"high":12,"medium":6,"low":0}.get(consistency, 0)
    if cs:                   insights.append("  Consistency → preparation stability")
    if hackathons == "yes":  insights.append("  Hackathons → competitive exposure")
    if open_source == "yes": insights.append("  Open source → collaboration proof")
    if certifications=="yes":insights.append("  Certifications → validated skills")

    if core_score >= 15:        strengths.append("Strong CS fundamentals")
    if dsa_score  >= 16:        strengths.append("Solid DSA foundation")
    if projects  == "yes":      strengths.append("Hands-on project experience")
    if internship == "yes":     strengths.append("Real industry exposure")
    if hackathons == "yes":     strengths.append("Hackathon competitive experience")
    if open_source == "yes":    strengths.append("Open source contributions")
    if certifications == "yes": strengths.append("Industry certifications")
    if linkedin == "yes":       strengths.append("Active LinkedIn presence")

    if core_score < 10:      gaps.append("Weak core CS subjects")
    if dsa_score  < 12:      gaps.append("Limited DSA depth")
    if projects  == "no":    gaps.append("No project portfolio")
    if internship == "no":   gaps.append("No internship experience")
    if consistency == "low": gaps.append("Irregular practice habit")
    if aptitude == "weak":   gaps.append("Aptitude/quant skills need work")
    if linkedin == "no":     gaps.append("No LinkedIn presence")

    if dsa_score  < 10:      risks.append("Coding rounds may be difficult")
    if projects  == "no":    risks.append("Weak practical narrative in interviews")
    if internship == "no":   risks.append("Less competitive resume")
    if aptitude == "weak":   risks.append("May struggle in off-campus aptitude tests")

    improved = score
    if projects  == "no":  improved += 10; future.append("Adding a project → +10 pts")
    if internship == "no": improved += 12; future.append("Getting internship → +12 pts")
    if dsa_score  < 20:    improved += 8;  future.append("Improving DSA → better coding rounds")
    if hackathons == "no": improved += 5;  future.append("Joining a hackathon → +5 pts")
    improved = min(100, improved)

    if dsa_score  < 20:      actions.append("Solve 2 DSA problems daily on LeetCode")
    if projects  == "no":    actions.append("Build one complete real-world project this month")
    if internship == "no":   actions.append("Apply for internships on Internshala / LinkedIn")
    if consistency == "low": actions.append("Set a daily 2-hour structured study routine")
    if aptitude   == "weak": actions.append("Practice quant + reasoning on IndiaBIX daily")
    if not actions:          actions = ["Start advanced DSA", "Take mock interviews", "Apply to top companies"]

    lines = (
        ["PERSONALIZED INSIGHT:", f"Your current placement readiness is {score}%.", ""]
        + ["DETAILED ANALYSIS:"] + insights + [""]
        + ["STRENGTHS:"] + ([f"- {s}" for s in strengths] or ["- Building stage"]) + [""]
        + ["GAPS:"]      + ([f"- {g}" for g in gaps]      or ["- Minor improvements only"]) + [""]
        + ["RISKS:"]     + ([f"- {r}" for r in risks]      or ["- No major risks"]) + [""]
        + ["FUTURE IMPACT (if you act now):"]
        + ([f"- {f}" for f in future] or ["- Already near optimal"])
        + ["", f"EXPECTED IMPROVED SCORE: ~{improved}%", ""]
        + ["ACTION PLAN:"]
        + [f"{i+1}. {actions[min(i,len(actions)-1)]}" for i in range(3)]
    )
    return "\n".join(lines)


# ── RICH CAREER GUIDANCE LIBRARY ─────────────────────────────────────────────
GUIDANCE_LIBRARY = {
    "software":   {"title":"Software Development","path":"Junior Dev → SDE → Senior SDE → Tech Lead → Architect","skills":["Python/Java/C++","DSA","System Design","Git & CI/CD","SQL","REST APIs","Docker/K8s"],"companies":["Google","Microsoft","Amazon","Zoho","Flipkart","Razorpay"],"steps":["Master one language deeply — Python or Java","Solve 300+ LeetCode problems systematically","Build 2–3 full-stack projects with clean GitHub repos","Study system design: load balancers, caching, DBs at scale","Practice mock interviews weekly on Pramp","Apply to 20+ companies — track in a spreadsheet","Negotiate offers — research market value on levels.fyi"]},
    "data":       {"title":"Data Science / Analytics","path":"Data Analyst → Data Scientist → Senior DS → ML Engineer","skills":["Python (Pandas/NumPy)","SQL (advanced)","Statistics","Machine Learning","Power BI / Tableau","TensorFlow"],"companies":["Google","Amazon","Mu Sigma","Tiger Analytics","Fractal","Razorpay"],"steps":["Get fluent in SQL first — used in every data job","Learn Python for data: Pandas, Matplotlib, Scikit-learn","Build one end-to-end ML project on a real Kaggle dataset","Create 3 dashboards/analysis projects for your portfolio","Study statistics: hypothesis testing, regression, distributions","Get Google Data Analytics or IBM Data Science cert","Network with data professionals on LinkedIn and apply"]},
    "ml":         {"title":"AI / Machine Learning","path":"ML Engineer → Senior MLE → Research Scientist → Principal Researcher","skills":["Python","PyTorch / TensorFlow","Linear Algebra & Calculus","NLP / Computer Vision","MLOps","Cloud (AWS/GCP)"],"companies":["Google DeepMind","Microsoft Research","Amazon","Nvidia","Persistent"],"steps":["Build strong Python + math foundations first","Complete Andrew Ng's Deep Learning Specialization","Implement ML papers from scratch — start with ResNet","Contribute to open source ML projects on GitHub","Participate in Kaggle competitions — aim for a medal","Build an end-to-end MLOps pipeline with model serving","Read ArXiv papers weekly to stay current"]},
    "web":        {"title":"Web Development","path":"Junior Dev → Frontend/Backend → Full Stack → Senior → Lead","skills":["HTML/CSS/JavaScript","React or Vue","Node.js / Django","PostgreSQL / MongoDB","Docker","AWS / Vercel"],"companies":["Zoho","Freshworks","Razorpay","Swiggy","Hotstar","Atlassian"],"steps":["Master HTML, CSS, and vanilla JavaScript — no shortcuts","Learn React + Node.js/Express or Django for backend","Deploy 3 full-stack projects with live URLs","Learn databases: PostgreSQL + MongoDB","Understand DevOps basics: Docker, CI/CD","Build a personal portfolio site, keep your GitHub green","Apply to startups first for faster broad exposure"]},
    "cloud":      {"title":"Cloud & DevOps","path":"Cloud Support → Cloud Engineer → DevOps → Senior → Architect","skills":["AWS/GCP/Azure","Docker & Kubernetes","Terraform","Linux","CI/CD (GitHub Actions)","Python scripting"],"companies":["Amazon AWS","Microsoft Azure","Google Cloud","TCS","Infosys","Wipro"],"steps":["Get AWS Cloud Practitioner certified first","Learn Linux command line and bash scripting deeply","Master Docker + Kubernetes with hands-on labs on KodeKloud","Learn Infrastructure-as-Code with Terraform","Build a CI/CD pipeline for a real project end-to-end","Get AWS Solutions Architect Associate certification","Practice cloud architecture case studies for interviews"]},
    "cyber":      {"title":"Cybersecurity","path":"Security Analyst → Pen Tester → Security Engineer → CISO","skills":["Networking (TCP/IP, DNS)","Linux","Python","OWASP Top 10","Kali Linux","SIEM tools","CEH / CISSP"],"companies":["Cisco","Palo Alto Networks","IBM Security","TCS","HCL"],"steps":["Learn networking: OSI model, TCP/IP, DNS, HTTP/S","Set up a home lab with Kali Linux — use TryHackMe","Study OWASP Top 10 web vulnerabilities","Get CompTIA Security+ certification as a baseline","Learn Python for scripting and automation","Participate in CTF competitions on CTFtime","Aim for CEH or OSCP for career-defining credibility"]},
    "mech":       {"title":"Mechanical Engineering","path":"Graduate Trainee → Design Engineer → Senior Engineer → Manager","skills":["SolidWorks / CATIA / AutoCAD","ANSYS / FEM Analysis","Thermodynamics","Manufacturing Processes","GD&T","Project Management"],"companies":["Bosch","Tata Motors","Mahindra","L&T","Siemens","Cummins"],"steps":["Master one CAD tool: SolidWorks or CATIA — become fluent","Learn FEM/FEA with ANSYS for simulation roles","Complete core subjects: thermodynamics, fluid mechanics","Do an internship at a manufacturing company","Get a GATE score for PSU jobs or higher studies","Build a project (drone, robot, automation) to showcase skills","Network at ASME or SAE events for referrals"]},
    "bio":        {"title":"Biotechnology","path":"Research Assistant → Associate Scientist → Scientist → Director R&D","skills":["Molecular Biology","Bioinformatics","PCR / ELISA","Cell Culture","Python for Bio","Regulatory Affairs"],"companies":["Biocon","Dr. Reddy's","Sun Pharma","Serum Institute","Syngene"],"steps":["Build strong wet lab skills: PCR, gel electrophoresis, cell culture","Learn bioinformatics tools: BLAST, NCBI, Biopython","Publish or assist in research — even a poster counts","Get certified in GLP or GMP","Network on LinkedIn and ResearchGate with biotech professionals","Apply for CSIR-NET or DBT JRF for research track","Consider a Masters or PhD for core research roles"]},
    "finance":    {"title":"Finance & Accounting","path":"Financial Analyst → Senior Analyst → Associate → VP → CFO","skills":["Financial Modeling","Excel (Advanced)","Python for Finance","Accounting (GAAP/IFRS)","Bloomberg Terminal","Valuation"],"companies":["Goldman Sachs","JP Morgan","Deloitte","KPMG","HDFC","ICICI"],"steps":["Master Excel: VLOOKUP, pivot tables, financial modeling","Learn financial statements: P&L, Balance Sheet, Cash Flow","Build a DCF valuation model for a public company","Get CFA Level 1 or CA Foundation to signal seriousness","Learn Python for finance: yfinance, pandas for stock analysis","Network with finance professionals and alumni on LinkedIn","Apply for analyst roles at Big 4, banks, and PE/VC firms"]},
    "design":     {"title":"UI/UX Design","path":"Junior Designer → UX Designer → Senior UX → Lead → Head of Design","skills":["Figma / Adobe XD","User Research","Prototyping","Design Systems","HTML/CSS basics","Usability Testing"],"companies":["Adobe","Swiggy","Zomato","Freshworks","Razorpay","Google"],"steps":["Learn Figma deeply — the industry standard for UI/UX","Study UX fundamentals: user research, personas, journey maps","Redesign 3 existing apps and document your design thinking","Build a case study portfolio on Behance or a personal site","Run a usability test and document your findings","Learn basic HTML/CSS — it makes you much more valuable","Apply to startups and agencies for faster hands-on growth"]},
}

def get_guidance_data(interest):
    interest_lower = interest.lower()
    for key, data in GUIDANCE_LIBRARY.items():
        if key in interest_lower or any(w in interest_lower for w in data["title"].lower().split()):
            return data
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    msg = ""
    if request.args.get("reset"):
        msg = "Password reset successfully. Please log in."

    if request.method == "POST":
        if request.form.get("guest"):
            session.update({"user":"Guest","user_id":None,"is_guest":True,"active_domain_id":None})
            return redirect("/home")

        identifier = request.form.get("username","").strip()
        password   = request.form.get("password","")
        db = get_db(); c = db.cursor(dictionary=True)
        c.execute(
            "SELECT id, username, active_domain_id FROM users "
            "WHERE (username=%s OR email=%s) AND password=%s",
            (identifier, identifier, password)
        )
        user = c.fetchone(); c.close(); db.close()
        if user:
            session.update({"user":user["username"],"user_id":user["id"],
                            "active_domain_id":user["active_domain_id"],"is_guest":False})
            return redirect("/home")
        return render_template("login.html", error="Invalid username/email or password")

    return render_template("login.html", success=msg)


@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        username  = request.form.get("username","").strip()
        email     = request.form.get("email","").strip()
        password  = request.form.get("password","")
        fullname  = request.form.get("fullname","").strip()
        mobile    = request.form.get("mobile","").strip() or None
        pref_type = request.form.get("pref_type","both")
        domain    = request.form.get("domain","Computer Engineering").strip()

        if not email or "@" not in email or "." not in email:
            return render_template("signup.html", error="Invalid email format")

        db = get_db(); c = db.cursor(dictionary=True)
        c.execute("SELECT id FROM users WHERE username=%s OR email=%s", (username, email))
        if c.fetchone():
            c.close(); db.close()
            return render_template("signup.html", error="Username or email already registered")

        c2 = db.cursor()
        try:
            c2.execute("INSERT INTO users (username,email,password) VALUES (%s,%s,%s)",
                       (username, email, password))
            db.commit()
            uid = c2.lastrowid
            c2.execute("INSERT INTO user_domains (user_id,domain_name,is_primary) VALUES (%s,%s,1)",
                       (uid, domain))
            db.commit()
            did = c2.lastrowid
            c2.execute("UPDATE users SET active_domain_id=%s WHERE id=%s", (did, uid))
            db.commit()
        except Exception as e:
            c.close(); c2.close(); db.close()
            return render_template("signup.html", error=str(e))

        c.close(); c2.close(); db.close()
        send_welcome_email(email, username, domain)
        return redirect("/")

    return render_template("signup.html")


@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    msg = None
    if request.method == "POST":
        email = request.form.get("email","").strip()
        db = get_db(); c = db.cursor(dictionary=True)
        c.execute("SELECT id, email FROM users WHERE email=%s", (email,))
        user = c.fetchone()
        if user:
            token = ''.join(random.choices(string.ascii_letters + string.digits, k=48))
            c2 = db.cursor()
            c2.execute("INSERT INTO password_resets (user_id,token) VALUES (%s,%s) "
                       "ON DUPLICATE KEY UPDATE token=%s, created_at=NOW()",
                       (user["id"], token, token))
            db.commit(); c2.close()
            send_reset_email(email, token)
        c.close(); db.close()
        msg = "If that email is registered, a reset link has been sent."
    return render_template("forgot_password.html", msg=msg)


@app.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    db = get_db(); c = db.cursor(dictionary=True)
    c.execute("SELECT user_id FROM password_resets "
              "WHERE token=%s AND created_at > NOW() - INTERVAL 1 HOUR", (token,))
    row = c.fetchone()
    if not row:
        c.close(); db.close()
        return render_template("reset_password.html", valid=False, token=token)
    if request.method == "POST":
        pw = request.form.get("password","")
        c2 = db.cursor()
        c2.execute("UPDATE users SET password=%s WHERE id=%s", (pw, row["user_id"]))
        c2.execute("DELETE FROM password_resets WHERE token=%s", (token,))
        db.commit(); c2.close(); c.close(); db.close()
        return redirect("/?reset=1")
    c.close(); db.close()
    return render_template("reset_password.html", valid=True, token=token, error=None)


# ─────────────────────────────────────────────────────────────────────────────
#  DOMAINS
# ─────────────────────────────────────────────────────────────────────────────

AVAILABLE_DOMAINS = [
    "Computer Engineering","Electronics & Communication","Mechanical Engineering",
    "Civil Engineering","Biotechnology","Chemical Engineering",
    "Electrical Engineering","Information Technology","Business Administration",
    "Finance & Accounting","Data Science & AI","Design & UX",
    "Healthcare & Medicine","Law & Legal Studies","Marketing & Media"
]

@app.route("/domains")
def domains():
    if "user" not in session or session.get("is_guest"): return redirect("/")
    db = get_db(); c = db.cursor(dictionary=True)
    c.execute("SELECT * FROM user_domains WHERE user_id=%s ORDER BY is_primary DESC", (session["user_id"],))
    user_domains = c.fetchall(); c.close(); db.close()
    return render_template("domains.html", user_domains=user_domains,
        active_id=session.get("active_domain_id"), available=AVAILABLE_DOMAINS)

@app.route("/domains/switch/<int:did>")
def switch_domain(did):
    if "user" not in session or session.get("is_guest"): return redirect("/")
    db = get_db(); c = db.cursor()
    c.execute("UPDATE users SET active_domain_id=%s WHERE id=%s "
              "AND EXISTS (SELECT 1 FROM user_domains WHERE id=%s AND user_id=%s)",
              (did, session["user_id"], did, session["user_id"]))
    db.commit(); c.close(); db.close()
    session["active_domain_id"] = did
    return redirect("/home")

@app.route("/domains/add", methods=["POST"])
def add_domain():
    if "user" not in session or session.get("is_guest"): return redirect("/")
    domain = request.form.get("domain","").strip()
    if domain:
        db = get_db(); c = db.cursor()
        c.execute("INSERT IGNORE INTO user_domains (user_id,domain_name,is_primary) VALUES (%s,%s,0)",
                  (session["user_id"], domain))
        db.commit(); c.close(); db.close()
    return redirect("/domains")


# ─────────────────────────────────────────────────────────────────────────────
#  HOME
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/home")
def home():
    if "user" not in session: return redirect("/")
    if session.get("is_guest"):
        return render_template("home.html", username="Guest", active_domain="General",
            last_score=None, level=1, level_name="Newcomer", level_icon="⚪",
            xp=0, next_xp=50, streak=0, is_guest=True)

    db = get_db(); c = db.cursor(dictionary=True)
    c.execute("SELECT domain_name FROM user_domains WHERE id=%s", (session.get("active_domain_id"),))
    row = c.fetchone()
    c.execute("SELECT score FROM analysis_history WHERE user_id=%s ORDER BY created_at DESC LIMIT 1",
              (session["user_id"],))
    last = c.fetchone()
    c.execute("SELECT COUNT(*) cnt FROM assessment_results WHERE user_id=%s", (session["user_id"],))
    atotal = c.fetchone()["cnt"]
    c.execute("SELECT MAX(score_pct) best FROM assessment_results WHERE user_id=%s", (session["user_id"],))
    abest = c.fetchone()["best"] or 0
    c.close(); db.close()

    level, level_name, level_icon, xp, next_xp = compute_level(atotal, abest)
    streak = compute_streak(session["user_id"])

    return render_template("home.html",
        username=session["user"],
        active_domain=row["domain_name"] if row else "General",
        last_score=last["score"] if last else None,
        level=level, level_name=level_name, level_icon=level_icon,
        xp=xp, next_xp=next_xp, streak=streak, is_guest=False)


# ─────────────────────────────────────────────────────────────────────────────
#  PLACEMENT ANALYZER
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/placement", methods=["GET","POST"])
def placement():
    if "user" not in session: return redirect("/")
    if session.get("is_guest"): return render_template("guest_wall.html", feature="Placement Analyzer")

    score=None; ai_feedback=""; probability=""

    if request.method == "POST":
        core_skills    = request.form.getlist("core_skills")
        dsa            = request.form.getlist("dsa_concepts")
        projects       = request.form.get("projects","no")
        internship     = request.form.get("internship","no")
        consistency    = request.form.get("consistency","low")
        resume         = request.form.get("resume","average")
        communication  = request.form.get("communication","average")
        mock           = request.form.get("mock","no")
        hackathons     = request.form.get("hackathons","no")
        open_source    = request.form.get("open_source","no")
        certifications = request.form.get("certifications","no")
        linkedin       = request.form.get("linkedin","no")
        aptitude       = request.form.get("aptitude","average")
        coding_platform= request.form.get("coding_platform","none")
        backlogs       = request.form.get("backlogs","no")
        cgpa           = request.form.get("cgpa","")

        score  = (len(core_skills)/4)*20 + (len(dsa)/6)*24
        score += 10 if projects=="yes" else 0
        score += 12 if internship=="yes" else 0
        score += {"high":12,"medium":6,"low":0}.get(consistency,0)
        score += 8 if resume=="good" else 0
        score += 8 if communication=="good" else 0
        score += 10 if mock=="yes" else 0
        score += 5 if hackathons=="yes" else 0
        score += 4 if open_source=="yes" else 0
        score += 3 if certifications=="yes" else 0
        score += 3 if linkedin=="yes" else 0
        score += {"leetcode":5,"codeforces":7,"both":8}.get(coding_platform,0)
        score -= 5 if backlogs=="yes" else 0
        score = round(min(max(score,0),100))

        probability = placement_probability(score)
        ai_feedback = generate_smart_feedback(score,core_skills,dsa,projects,internship,
                        consistency,cgpa,hackathons,open_source,certifications,linkedin,aptitude)

        ai_resp = call_ai(
            f"A student has {score}% placement readiness. Core: {core_skills}. DSA: {dsa}. "
            f"Projects: {projects}. Internship: {internship}. Hackathons: {hackathons}. "
            f"Open source: {open_source}. Certifications: {certifications}. Backlogs: {backlogs}. "
            f"Aptitude: {aptitude}. Coding platform: {coding_platform}. "
            f"Give 4 specific, actionable improvement suggestions in bullet points. Be direct and concise.",
            max_tokens=280
        )
        if ai_resp: ai_feedback = ai_resp

        db = get_db(); c = db.cursor()
        try:
            c.execute("INSERT INTO analysis_history (user_id,domain_id,score,probability) VALUES (%s,%s,%s,%s)",
                      (session["user_id"], session.get("active_domain_id"), score, probability))
            db.commit()
        except Exception: pass
        c.close(); db.close()

    return render_template("placement.html", score=score, ai_feedback=ai_feedback, probability=probability)


# ─────────────────────────────────────────────────────────────────────────────
#  SKILL GAP
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/skill-gap", methods=["GET","POST"])
def skill_gap():
    if "user" not in session: return redirect("/")
    if session.get("is_guest"): return render_template("guest_wall.html", feature="Skill Gap Detection")

    recommendations=[]; role=""; skills_input=""; readiness=None; roadmap_steps=[]

    if request.method == "POST":
        role         = request.form.get("role","")
        skills_input = request.form.get("skills","")
        current      = [s.strip() for s in skills_input.split(",") if s.strip()]
        recommendations = recommend_skills(current, role)
        readiness       = calculate_readiness_score(current, role, JOB_SKILL_MAP)
        roadmap_steps   = generate_roadmap(recommendations)

    return render_template("skill_gap.html",
        roles=JOB_SKILL_MAP.keys(), recommendations=recommendations,
        selected_role=role, skills_input=skills_input,
        readiness=readiness, roadmap=roadmap_steps)


# ─────────────────────────────────────────────────────────────────────────────
#  ROADMAP
# ─────────────────────────────────────────────────────────────────────────────

ROADMAPS = {
    "beginner":     ["Study your target role's job descriptions carefully","Learn programming fundamentals: variables, loops, functions, OOP","Study core subjects: DSA, DBMS, OS, Computer Networks","Solve 100+ DSA problems (easy → medium) on LeetCode","Build 1–2 beginner projects and push them to GitHub","Create/update your resume and LinkedIn profile","Apply to internships and entry-level roles"],
    "intermediate": ["Research target companies and their hiring process in depth","Solve 200+ DSA problems (medium/hard: trees, graphs, DP)","Build 2+ real-world projects with proper documentation","Learn role-specific tools (Spring Boot, React, SQL, etc.)","Do 5+ mock interviews and optimise your resume for ATS","Study system design fundamentals","Target 10+ companies with a structured application tracker"]
}

@app.route("/roadmap", methods=["GET","POST"])
def roadmap():
    if "user" not in session: return redirect("/")
    steps=[]; role=""; experience=""
    if request.method == "POST":
        role       = request.form.get("role","").strip()
        experience = request.form.get("experience","beginner")
        ai_steps = call_ai(
            f"Create a detailed 7-step learning roadmap for a {role} role at {experience} level. "
            f"Number each step. One line per step. Be specific.", max_tokens=350
        )
        if ai_steps:
            steps = [s.strip() for s in ai_steps.split("\n") if s.strip()][:10]
        if not steps:
            steps = [f"Set clear goal: target {role} roles"] + ROADMAPS.get(experience, ROADMAPS["beginner"])
    return render_template("roadmap.html", steps=steps, role=role, experience=experience)


# ─────────────────────────────────────────────────────────────────────────────
#  CAREER GUIDANCE
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/guidance", methods=["GET","POST"])
def guidance():
    if "user" not in session: return redirect("/")
    advice=None; interest=""; guidance_data=None

    if request.method == "POST":
        interest = request.form.get("interest","").strip()
        guidance_data = get_guidance_data(interest)

        if not guidance_data:
            raw = call_ai(
                f"You are a senior career counselor. Give structured guidance for '{interest}'. "
                f"Return ONLY a JSON object with keys: title, path, skills (array 6), "
                f"companies (array 5), steps (array 7). No markdown. Just JSON.",
                max_tokens=600
            )
            if raw:
                try:
                    clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
                    guidance_data = json.loads(clean)
                except Exception:
                    pass

        if not guidance_data:
            advice = (f"Build strong {interest} fundamentals, create real projects, "
                      f"network consistently, and apply strategically.")

    return render_template("guidance.html", advice=advice, interest=interest, guidance_data=guidance_data)


# ─────────────────────────────────────────────────────────────────────────────
#  AI ASSESSMENT
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/assessment")
def assessment():
    if "user" not in session: return redirect("/")
    if session.get("is_guest"): return render_template("guest_wall.html", feature="AI Assessment")

    db = get_db(); c = db.cursor(dictionary=True)
    c.execute("SELECT domain_name FROM user_domains WHERE id=%s", (session.get("active_domain_id"),))
    drow = c.fetchone()
    domain = drow["domain_name"] if drow else "General"

    c.execute("SELECT topic FROM assessment_results "
              "WHERE user_id=%s AND domain_id=%s AND score_pct>=70 GROUP BY topic",
              (session["user_id"], session.get("active_domain_id")))
    passed = [r["topic"] for r in c.fetchall()]

    c.execute("SELECT topic, MAX(score_pct) best, COUNT(*) attempts "
              "FROM assessment_results WHERE user_id=%s AND domain_id=%s "
              "GROUP BY topic ORDER BY best DESC",
              (session["user_id"], session.get("active_domain_id")))
    topic_stats = c.fetchall()
    c.close(); db.close()

    return render_template("assessment.html", domain=domain, passed_topics=passed, topic_stats=topic_stats)


@app.route("/assessment/start", methods=["POST"])
def assessment_start():
    if "user" not in session or session.get("is_guest"): return redirect("/")

    topic = request.form.get("topic","").strip()
    if not topic: return redirect("/assessment")

    db = get_db(); c = db.cursor(dictionary=True)
    c.execute("SELECT domain_name FROM user_domains WHERE id=%s", (session.get("active_domain_id"),))
    drow = c.fetchone()
    domain = drow["domain_name"] if drow else "General"

    # Collect all past question texts to avoid repeating them
    c.execute("SHOW COLUMNS FROM assessment_results LIKE 'questions_asked'")
    has_qa = c.fetchone() is not None

    already_asked = []
    if has_qa:
        c.execute("SELECT questions_asked FROM assessment_results "
                  "WHERE user_id=%s AND domain_id=%s AND topic=%s AND questions_asked IS NOT NULL",
                  (session["user_id"], session.get("active_domain_id"), topic))
        for row in c.fetchall():
            try: already_asked.extend(json.loads(row["questions_asked"] or "[]"))
            except Exception: pass

    c.execute("SELECT topic FROM assessment_results "
              "WHERE user_id=%s AND domain_id=%s AND score_pct<70 GROUP BY topic",
              (session["user_id"], session.get("active_domain_id")))
    weak = [r["topic"] for r in c.fetchall()]
    c.close(); db.close()

    avoid_q  = f"IMPORTANT: Do NOT repeat any of these previously asked questions: {already_asked[:15]}. " if already_asked else ""
    revisit  = f"The student has previously struggled with {topic}. Make questions slightly harder." if topic in weak else ""

    prompt = (
        f"Generate exactly 10 assessment questions for the topic '{topic}' in the context of {domain}. "
        f"{avoid_q}{revisit}"
        f"Mix: exactly 8 multiple-choice questions AND exactly 2 coding/problem-solving questions. "
        f"MCQs must have 4 options and exactly one correct answer. "
        f"Coding questions should ask the student to write code or explain an algorithm. "
        f"Return ONLY a valid JSON array. No markdown, no preamble, just the array. "
        f'Use this format: [{{"type":"mcq","q":"...","options":["A)...","B)...","C)...","D)..."],"answer":"A)...","explanation":"..."}}, '
        f'{{"type":"code","q":"Write a function to ...","answer":"Expected approach: ...","explanation":"..."}}]'
    )

    raw = call_ai(prompt, max_tokens=1500)
    questions = []
    if raw:
        try:
            clean = raw.strip()
            if clean.startswith("```"):
                parts = clean.split("```")
                clean = parts[1] if len(parts) > 1 else clean
                if clean.startswith("json"): clean = clean[4:]
            questions = json.loads(clean.strip())
        except Exception:
            questions = []

    if not questions:
        questions = [
            {"type":"mcq","q":f"What is the time complexity of binary search in {topic}?",
             "options":["A) O(n)","B) O(log n)","C) O(n²)","D) O(1)"],
             "answer":"B) O(log n)","explanation":"Binary search halves the search space each step."},
            {"type":"mcq","q":f"Which data structure uses LIFO order?",
             "options":["A) Queue","B) Array","C) Stack","D) Linked List"],
             "answer":"C) Stack","explanation":"Stack follows Last In First Out (LIFO) principle."},
            {"type":"code","q":"Write a function to reverse a string in your preferred language.",
             "answer":"Use slicing in Python: return s[::-1], or loop from end to start.",
             "explanation":"Multiple approaches valid — focus on time/space complexity."},
        ]

    return render_template("assessment_quiz.html", topic=topic, questions=questions, domain=domain)


@app.route("/assessment/submit", methods=["POST"])
def assessment_submit():
    if "user" not in session or session.get("is_guest"): return redirect("/")

    topic          = request.form.get("topic","")
    correct_answers= json.loads(request.form.get("correct_answers","[]"))
    questions_data = json.loads(request.form.get("questions_data","[]"))
    questions_text = [q.get("q","") for q in questions_data]

    total=len(correct_answers); correct=0; results=[]
    mcq_total = sum(1 for q in questions_data if q.get("type","mcq")=="mcq")

    for i in range(total):
        user_ans = request.form.get(f"q_{i}","")
        ca       = correct_answers[i] if i < len(correct_answers) else ""
        qtype    = questions_data[i].get("type","mcq") if i < len(questions_data) else "mcq"
        is_correct = (user_ans.strip().lower() == ca.strip().lower()) if qtype=="mcq" else None
        if is_correct: correct += 1
        results.append({
            "question":      questions_data[i].get("q",f"Q{i+1}") if i < len(questions_data) else f"Q{i+1}",
            "your_answer":   user_ans,
            "correct_answer":ca,
            "explanation":   questions_data[i].get("explanation","") if i < len(questions_data) else "",
            "is_correct":    is_correct,
            "type":          qtype
        })

    score_pct = round((correct / mcq_total) * 100) if mcq_total else 0

    db = get_db(); c = db.cursor()
    # Add questions_asked column if not exists
    try:
        c.execute("ALTER TABLE assessment_results ADD COLUMN IF NOT EXISTS questions_asked JSON DEFAULT NULL")
        db.commit()
    except Exception: pass
    try:
        c.execute("INSERT INTO assessment_results "
                  "(user_id,domain_id,topic,score_pct,correct,total,questions_asked) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                  (session["user_id"], session.get("active_domain_id"), topic,
                   score_pct, correct, mcq_total, json.dumps(questions_text)))
        db.commit()
    except Exception: pass
    c.close(); db.close()

    return render_template("assessment_result.html",
        topic=topic, score_pct=score_pct, correct=correct, total=mcq_total, results=results)


# ─────────────────────────────────────────────────────────────────────────────
#  LEADERBOARD
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/leaderboard")
def leaderboard():
    if "user" not in session: return redirect("/")

    db = get_db(); c = db.cursor(dictionary=True)
    c.execute("""
        SELECT u.username,
               COUNT(ar.id)                  AS total_assessments,
               ROUND(AVG(ar.score_pct),1)    AS avg_score,
               MAX(ar.score_pct)             AS best_score,
               SUM(ar.correct)               AS total_correct,
               COUNT(DISTINCT ar.topic)      AS topics_covered
        FROM assessment_results ar
        JOIN users u ON u.id = ar.user_id
        GROUP BY ar.user_id, u.username
        ORDER BY (COUNT(ar.id) * 10 + MAX(ar.score_pct)) DESC
        LIMIT 50
    """)
    board = c.fetchall()
    c.close(); db.close()

    enriched = []
    for i, row in enumerate(board):
        lv, lname, licon, xp, _ = compute_level(row["total_assessments"], row["best_score"])
        enriched.append({**row, "rank":i+1, "level":lv, "level_name":lname, "level_icon":licon, "xp":xp})

    my_rank = next((e["rank"] for e in enriched if e["username"]==session.get("user")), None)
    return render_template("leaderboard.html", board=enriched, my_rank=my_rank)


# ─────────────────────────────────────────────────────────────────────────────
#  COMPANIES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/companies", methods=["GET","POST"])
def companies():
    if "user" not in session: return redirect("/")

    db = get_db(); c = db.cursor(dictionary=True)
    target_list = []

    if not session.get("is_guest"):
        c.execute("SELECT target_companies, pref_type FROM users WHERE id=%s", (session["user_id"],))
        urow = c.fetchone() or {}
        try: target_list = json.loads(urow.get("target_companies") or "[]")
        except Exception: target_list = []

        if request.method == "POST" and request.form.get("action") == "save_targets":
            targets = request.form.getlist("companies")
            c2 = db.cursor()
            c2.execute("UPDATE users SET target_companies=%s WHERE id=%s",
                       (json.dumps(targets), session["user_id"]))
            db.commit(); c2.close()
            target_list = targets
    else:
        urow = {"pref_type":"both"}

    c.execute("SELECT * FROM companies ORDER BY is_featured DESC, name ASC")
    all_companies = c.fetchall()
    c.close(); db.close()

    return render_template("companies.html",
        all_companies=all_companies, target_companies=target_list,
        pref_type=urow.get("pref_type","both") if not session.get("is_guest") else "both")


# ─────────────────────────────────────────────────────────────────────────────
#  PROGRESS DASHBOARD  ← BUG FIXED
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/progress")
def progress():
    if "user" not in session: return redirect("/")
    if session.get("is_guest"): return render_template("guest_wall.html", feature="Progress Dashboard")

    db = get_db(); c = db.cursor(dictionary=True)

    # Guard: check if domain_id column exists before using it
    c.execute("SHOW COLUMNS FROM analysis_history LIKE 'domain_id'")
    has_ah_domain = c.fetchone() is not None

    if has_ah_domain:
        c.execute(
            "SELECT score, probability, created_at FROM analysis_history "
            "WHERE user_id=%s AND (domain_id=%s OR domain_id IS NULL) "
            "ORDER BY created_at DESC LIMIT 10",
            (session["user_id"], session.get("active_domain_id"))
        )
    else:
        c.execute(
            "SELECT score, probability, created_at FROM analysis_history "
            "WHERE user_id=%s ORDER BY created_at DESC LIMIT 10",
            (session["user_id"],)
        )
    history = c.fetchall()

    c.execute("SHOW COLUMNS FROM assessment_results LIKE 'domain_id'")
    has_ar_domain = c.fetchone() is not None

    if has_ar_domain:
        c.execute(
            "SELECT topic, MAX(score_pct) best, COUNT(*) attempts "
            "FROM assessment_results "
            "WHERE user_id=%s AND (domain_id=%s OR domain_id IS NULL) "
            "GROUP BY topic ORDER BY best DESC",
            (session["user_id"], session.get("active_domain_id"))
        )
    else:
        c.execute(
            "SELECT topic, MAX(score_pct) best, COUNT(*) attempts "
            "FROM assessment_results WHERE user_id=%s "
            "GROUP BY topic ORDER BY best DESC",
            (session["user_id"],)
        )
    assessment_stats = c.fetchall()

    c.execute("SELECT domain_name FROM user_domains WHERE id=%s", (session.get("active_domain_id"),))
    drow = c.fetchone()
    c.close(); db.close()

    total_a = sum(s["attempts"] for s in assessment_stats)
    best_a  = max((s["best"] for s in assessment_stats), default=0)
    level, level_name, level_icon, xp, next_xp = compute_level(total_a, best_a)

    return render_template("progress.html",
        history=history, assessment_stats=assessment_stats,
        domain=drow["domain_name"] if drow else "General",
        latest_score=history[0]["score"] if history else 0,
        level=level, level_name=level_name, level_icon=level_icon,
        xp=xp, next_xp=next_xp)


# ─────────────────────────────────────────────────────────────────────────────
#  LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)