from persona.manager import PersonaManager
import logging

# Setup basic logging to see output
logging.basicConfig(level=logging.INFO)

def main():
    print("--- Verifying Seed Persona Loading ---")

    # Initialize manager
    manager = PersonaManager(sheet_path="Seed-AI/Seed_sheet.yaml")

    # Load persona
    manager.load_persona()

    # Get system prompt
    prompt = manager.get_system_prompt()

    print("\n--- GENERATED SYSTEM PROMPT ---")
    print(prompt)
    print("\n--- END OF PROMPT ---")

    # Check for keywords
    keywords = ["Seed", "Seed Sr.", "Yah-hoh~", "scents", "tinkering"]
    print("\n--- KEYWORD CHECK ---")
    for kw in keywords:
        found = kw in prompt
        print(f"Keyword '{kw}': {'FOUND' if found else 'NOT FOUND'}")

    # Check for forbidden terms (Fenilux terms should be gone)
    forbidden = ["Fenilux", "cheesecake", "macaroni", "Goddess", "Diva"]
    print("\n--- FORBIDDEN TERM CHECK ---")
    for ft in forbidden:
        found = ft in prompt
        print(f"Forbidden term '{ft}': {'FOUND (ERROR)' if found else 'NOT FOUND (OK)'}")

if __name__ == "__main__":
    main()
