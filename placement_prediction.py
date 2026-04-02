# placement_prediction.py

def calculate_readiness_score(current_skills, target_role, job_skill_map):
    required = job_skill_map.get(target_role, [])
    if not required:
        return 0

    normalized_current = [s.lower() for s in current_skills]
    matched = sum(1 for skill in required if skill.lower() in normalized_current)

    score = (matched / len(required)) * 100
    return round(score, 2)


