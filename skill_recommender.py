# skill_recommender.py

JOB_SKILL_MAP = {
    "Software Developer": [
        "Java", "Python", "SQL", "Spring Boot", "Git", "Data Structures"
    ],
    "Backend Developer": [
        "Java", "Spring Boot", "REST APIs", "SQL", "Hibernate", "Git"
    ],
    "Data Analyst": [
        "Python", "SQL", "Excel", "Statistics", "Power BI", "Pandas"
    ],
    "UI/UX Designer": [
        "Figma", "Wireframing", "User Research", "Prototyping", "Design Thinking"
    ],
    "Digital Marketer": [
        "SEO", "Content Marketing", "Google Analytics", "Social Media", "Ads"
    ],
    "Business Analyst": [
        "Requirement Analysis", "SQL", "Excel", "Communication", "Documentation"
    ],
    "Content Writer": [
        "SEO Writing", "Research", "Grammar", "Creativity", "Editing"
    ]
}


def recommend_skills(current_skills, role):
    required_skills = JOB_SKILL_MAP.get(role, [])
    current_skills_lower = [s.lower() for s in current_skills]

    missing_skills = []
    for skill in required_skills:
        if skill.lower() not in current_skills_lower:
            missing_skills.append(skill)

    return missing_skills


def placement_probability(readiness):
    if readiness >= 80:
        return "High"
    elif readiness >= 50:
        return "Medium"
    else:
        return "Low"


def generate_roadmap(missing_skills):
    roadmap = []
    for i, skill in enumerate(missing_skills, start=1):
        roadmap.append(f"Step {i}: Learn {skill}")
    return roadmap
