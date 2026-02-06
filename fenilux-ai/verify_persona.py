from persona.manager import PersonaManager
import logging

# Setup basic logging to see output
logging.basicConfig(level=logging.INFO)

def main():
    print("--- Verifying Fenilux Persona Loading ---")

    # Initialize manager
    # We specify the path relative to the root if running from the root
    manager = PersonaManager(sheet_path="fenilux-ai/fenilux_sheet.yaml")

    # Load persona
    manager.load_persona()

    # Get system prompt
    prompt = manager.get_system_prompt()

    print("\n--- GENERATED SYSTEM PROMPT ---")
    print(prompt)
    print("\n--- END OF PROMPT ---")

    # Check for keywords
    keywords = ["Fenilux", "cake", "macaroni", "Crabaletta", "Usher", "Chevalmarin"]
    print("\n--- KEYWORD CHECK ---")
    for kw in keywords:
        found = kw in prompt
        print(f"Keyword '{kw}': {'FOUND' if found else 'NOT FOUND'}")

    # Check for forbidden terms
    forbidden = ["Fontaine", "Vision", "Neuvillette", "Navia", "Palais Mermonia", "Archon"]
    print("\n--- FORBIDDEN TERM CHECK (Genshin Terms) ---")
    for ft in forbidden:
        found = ft in prompt
        print(f"Forbidden term '{ft}': {'FOUND (ERROR)' if found else 'NOT FOUND (OK)'}")

if __name__ == "__main__":
    main()
