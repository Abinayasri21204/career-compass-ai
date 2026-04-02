from flask import Flask, render_template, request, redirect, session
import mysql.connector
import requests
from skill_recommender import recommend_skills, JOB_SKILL_MAP, placement_probability, generate_roadmap
from placement_prediction import calculate_readiness_score

app = Flask(__name__)
app.secret_key = "careercompass123"


# ── DB CONNECTION ─────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="asdzxc@12345",
        database="career_compass"
    )


# ── FAST AI CALL ─────────────────────────────────────────
def call_ai(prompt):
    try:
        response = requests.post(
            "http://127.0.0.1:11434/api/generate",
            json={
                "model": "gemma3:4b",
                "prompt": prompt,
                "stream": False
            },
            timeout=10
        )

        if response.status_code == 200:
            output = response.json().get("response", "").strip()
            if output:
                return output

    except:
        pass

    return None


# ── ADVANCED ANALYSIS ENGINE ─────────────────────────────
def generate_smart_feedback(score, core_skills, dsa, projects, internship, consistency):

    strengths = []
    gaps = []
    insights = []
    risks = []
    future = []

    core_score = (len(core_skills) / 4) * 20
    dsa_score = (len(dsa) / 6) * 24
    project_score = 10 if projects == "yes" else 0
    intern_score = 12 if internship == "yes" else 0
    consistency_score = {"high": 12, "medium": 6, "low": 0}.get(consistency, 0)

    intro = f"""
Based on your inputs, your current placement readiness stands at {score}%.

This score reflects your preparation pattern and current skill level.
"""

    # INSIGHTS
    insights.append("Your score is influenced by:")

    if core_score:
        insights.append(f"Core subjects → ~{int(core_score)} points")
    if dsa_score:
        insights.append(f"DSA → ~{int(dsa_score)} points")
    if project_score:
        insights.append("Projects → Practical exposure boost")
    if intern_score:
        insights.append("Internship → Industry advantage")
    if consistency_score:
        insights.append("Consistency → Stability in preparation")

    # STRENGTHS
    if core_score >= 15:
        strengths.append("Strong core CS fundamentals")
    if dsa_score >= 16:
        strengths.append("Good DSA problem solving")
    if projects == "yes":
        strengths.append("Hands-on project experience")
    if internship == "yes":
        strengths.append("Industry exposure")

    # GAPS
    if core_score < 10:
        gaps.append("Weak core subjects")
    if dsa_score < 12:
        gaps.append("Low DSA depth")
    if projects == "no":
        gaps.append("No project experience")
    if internship == "no":
        gaps.append("No internship exposure")
    if consistency == "low":
        gaps.append("Low consistency")

    # RISKS
    if dsa_score < 10:
        risks.append("May struggle in coding rounds")
    if projects == "no":
        risks.append("Weak real-world explanation in interviews")
    if internship == "no":
        risks.append("Resume may feel less competitive")

    # FUTURE IMPROVEMENT
    improved_score = score

    if projects == "no":
        improved_score += 10
        future.append("Adding a project → +10 score")
    if internship == "no":
        improved_score += 12
        future.append("Getting internship → +12 score")
    if dsa_score < 20:
        improved_score += 8
        future.append("Improving DSA → better coding success")

    improved_score = min(100, improved_score)

    # ACTIONS
    actions = []

    if dsa_score < 20:
        actions.append("Solve 2 DSA problems daily")
    if projects == "no":
        actions.append("Build a complete real-world project")
    if internship == "no":
        actions.append("Apply for internships")
    if consistency == "low":
        actions.append("Follow a strict daily schedule")

    if not actions:
        actions = [
            "Start advanced DSA",
            "Take mock interviews",
            "Apply to top companies"
        ]

    # ✅ SAFE STRING BUILDING (NO \n INSIDE {})
    insights_text = "\n".join(insights)
    strengths_text = "\n- ".join(strengths) if strengths else "Building stage"
    gaps_text = "\n- ".join(gaps) if gaps else "Minor improvements"
    risks_text = "\n- ".join(risks) if risks else "No major risks"
    future_text = "\n- ".join(future) if future else "Already near optimal"

    return f"""
PERSONALIZED INSIGHT:
{intro}

DETAILED ANALYSIS:
{insights_text}

STRENGTHS:
- {strengths_text}

GAPS:
- {gaps_text}

RISKS:
- {risks_text}

FUTURE IMPACT:
- {future_text}

EXPECTED IMPROVED SCORE: ~{improved_score}%

ACTION PLAN:
1. {actions[0]}
2. {actions[1] if len(actions)>1 else actions[0]}
3. {actions[2] if len(actions)>2 else actions[0]}
"""

# ── LOGIN ────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            "SELECT id, username FROM users WHERE username=%s AND password=%s",
            (username, password)
        )
        user = cursor.fetchone()

        cursor.close()
        db.close()

        if user:
            session["user"] = user["username"]
            session["user_id"] = user["id"]
            return redirect("/home")

        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")


# ── HOME ─────────────────────────────────────────────────
@app.route("/home")
def home():
    if "user" not in session:
        return redirect("/")
    return render_template("home.html", username=session["user"])


# ── PLACEMENT ANALYZER ───────────────────────────────────
@app.route("/placement", methods=["GET", "POST"])
def placement():
    if "user" not in session:
        return redirect("/")

    score = None
    ai_feedback = ""
    probability = ""

    if request.method == "POST":
        core_skills = request.form.getlist("core_skills")
        dsa = request.form.getlist("dsa_concepts")
        projects = request.form.get("projects")
        internship = request.form.get("internship")
        consistency = request.form.get("consistency")
        resume = request.form.get("resume")
        communication = request.form.get("communication")
        mock = request.form.get("mock")

        score = 0
        score += (len(core_skills)/4)*20
        score += (len(dsa)/6)*24
        score += 10 if projects=="yes" else 0
        score += 12 if internship=="yes" else 0
        score += {"high":12,"medium":6,"low":0}.get(consistency,0)
        score += 8 if resume=="good" else 0
        score += 8 if communication=="good" else 0
        score += 10 if mock=="yes" else 0

        score = round(min(score,100))
        probability = placement_probability(score)

        ai_feedback = generate_smart_feedback(
            score, core_skills, dsa, projects, internship, consistency
        )

        ai_response = call_ai(f"Give expert advice for score {score}")
        if ai_response:
            ai_feedback = ai_response

        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO analysis_history (user_id, score, probability) VALUES (%s,%s,%s)",
            (session["user_id"], score, probability)
        )
        db.commit()
        cursor.close()
        db.close()

    return render_template("placement.html", score=score, ai_feedback=ai_feedback, probability=probability)


# ── SKILL GAP ─────────────────────────────────────────────
@app.route("/skill-gap", methods=["GET","POST"])
def skill_gap():
    if "user" not in session:
        return redirect("/")

    recommendations=[]
    role=""
    skills_input=""
    readiness=None
    roadmap=[]

    if request.method=="POST":
        role=request.form.get("role")
        skills_input=request.form.get("skills")

        current_skills=[s.strip() for s in skills_input.split(",")]

        recommendations=recommend_skills(current_skills, role)
        readiness=calculate_readiness_score(current_skills, role, JOB_SKILL_MAP)
        roadmap=generate_roadmap(recommendations)

    return render_template("skill_gap.html",
        roles=JOB_SKILL_MAP.keys(),
        recommendations=recommendations,
        selected_role=role,
        skills_input=skills_input,
        readiness=readiness,
        roadmap=roadmap
    )


# ── ROADMAP ──────────────────────────────────────────────
@app.route("/roadmap", methods=["GET","POST"])
def roadmap():
    if "user" not in session:
        return redirect("/")

    steps=[]
    role=""
    experience=""

    if request.method=="POST":
        role=request.form.get("role")
        experience=request.form.get("experience")

        ai_steps=call_ai(f"Roadmap for {role} ({experience})")

        if ai_steps:
            steps=[s for s in ai_steps.split("\n") if s.strip()]
        else:
            steps=["Learn basics","Practice DSA","Build projects"]

    return render_template("roadmap.html", steps=steps, role=role, experience=experience)


# ── GUIDANCE ─────────────────────────────────────────────
@app.route("/guidance", methods=["GET","POST"])
def guidance():
    if "user" not in session:
        return redirect("/")

    advice=None
    interest=""

    if request.method=="POST":
        interest=request.form.get("interest")

        advice=call_ai(f"Career guidance for {interest}")

        if not advice:
            advice="Focus on fundamentals, build projects, and stay consistent."

    return render_template("guidance.html", advice=advice, interest=interest)


# ── LOGOUT ──────────────────────────────────────────────
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ── RUN ──────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)