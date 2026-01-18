def auto_continuity(category: str, clone_text: str, idea: str) -> dict:
    text = (clone_text + " " + idea).strip()

    # Minimal keyword cues
    is_animal = "Animals" in category
    is_diy = category == "DIY"
    is_shelter = category == "Shelter"
    is_survival = category == "Survival"
    is_bushcraft = category == "Bushcraft"

    # Actor lock (auto)
    if is_animal:
        actor = "Same wild animal subject across all scenes (species-accurate markings, consistent size and features)."
    elif is_diy:
        actor = "Same craftsperson across all scenes (consistent face/hands, same outfit: dark t-shirt + apron, same workspace behavior)."
    elif is_shelter or is_bushcraft or is_survival:
        actor = "Same outdoors person across all scenes (consistent identity, same outfit: olive jacket, cargo pants, boots, backpack)."
    else:
        actor = "Same main character across all scenes (consistent identity, outfit, hairstyle, props)."

    # Setting lock (auto)
    if is_animal:
        setting = "Same natural habitat throughout; camera keeps respectful distance; time-of-day evolves logically."
    elif is_diy:
        setting = "Same clean workshop and workbench throughout; tools remain consistent; lighting stays coherent."
    else:
        setting = "Same world continuity; location evolves logically scene-to-scene; weather/time shifts gradually."

    # Mood arc (auto)
    if is_survival:
        mood = "Rising tension → decision → action → relief."
    elif is_bushcraft or is_shelter:
        mood = "Calm focus → steady progress → satisfying completion."
    elif is_diy:
        mood = "Clean progress beats → satisfying reveal."
    elif is_animal:
        mood = "Observational calm → behavior highlight → calm exit."
    else:
        mood = "Cinematic build-up → turning point → resolution."

    rules = "Realistic physics. Keep continuity. No text/watermarks/logos. No random costume or environment jumps."

    return {"actor": actor, "setting": setting, "mood": mood, "rules": rules}
