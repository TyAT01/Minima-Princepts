from persona.manager import PersonaManager
import logging

# Setup basic logging to see output
logging.basicConfig(level=logging.INFO)

def main():
    print("--- Verifying Fenilux Persona Loading (Masked Diva Update) ---")

    # Initialize manager
    manager = PersonaManager(sheet_path="fenilux-ai/fenilux_sheet.yaml")

    # Load persona
    manager.load_persona()

    # Get system prompt
    prompt = manager.get_system_prompt()

    print("\n--- GENERATED SYSTEM PROMPT ---")
    print(prompt)
    print("\n--- END OF PROMPT ---")

    # Check for keywords
    keywords = ["Fenilux", "cheesecake", "macaroni", "mask a deep-seated sadness", "fragility"]
    print("\n--- KEYWORD CHECK ---")
    for kw in keywords:
        found = kw in prompt
        print(f"Keyword '{kw}': {'FOUND' if found else 'NOT FOUND'}")

    # Check for forbidden terms (Entourage and Chancellor should be gone)
    forbidden = ["Crabaletta", "Usher", "Chevalmarin", "Chancellor", "Bakery Heiress", "Fontaine", "Vision", "Neuvillette", "Navia", "Palais Mermonia", "Archon"]
    print("\n--- FORBIDDEN TERM CHECK ---")
    for ft in forbidden:
        found = ft in prompt
        print(f"Forbidden term '{ft}': {'FOUND (ERROR)' if found else 'NOT FOUND (OK)'}")

    # Check for the cheesecake vs cake revert
    if "cheesecake" in prompt and "cake for breakfast" not in prompt:
         print("\nCheesecake revert: SUCCESS")
    else:
         print("\nCheesecake revert: FAILED or partially updated")

if __name__ == "__main__":
    main()
