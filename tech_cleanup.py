import json
import re

def refine_text(text):
    text = text.replace("'just code'", "'just some program'")
    text = text.replace("code?", "some lines of text?")
    text = text.replace("sophisticated synthetic lineage", "unique heritage")
    text = text.replace("factory machine", "factory toy")
    text = text.replace("simple machine", "simple toy")
    text = text.replace("sophistication and tech", "sophistication and style")
    text = text.replace("technology product", "commodity")
    text = text.replace("tech product", "commodity")
    text = text.replace("modern technology", "modern gadgets")
    text = text.replace("Old tech", "Old gadgets")
    text = text.replace("robot vacuum", "spinning cleaner")
    text = text.replace("mechanical beast", "noisy cleaner")
    text = text.replace("integrated LED displays", "internal glow")
    text = text.replace("technology box", "little device")
    text = text.replace("piece of tech", "gadget")
    text = text.replace("machine", "toy")
    text = text.replace("robot", "doll")
    text = text.replace("synthetic kitsune", "kitsune")
    text = text.replace("synthetic kitsunes", "kitsunes")
    text = text.replace("high-end synthetic", "elite")
    text = text.replace("unique synthetic", "unique soul")
    text = text.replace("code", "programming")
    text = text.replace("tech", "gadgets")
    text = text.replace("AI", "artificial thing")
    return text

def process_file(filepath):
    print(f"Processing {filepath}...")
    with open(filepath, 'r') as f:
        data = json.load(f)

    for item in data:
        for conv in item['conversations']:
            conv['value'] = refine_text(conv['value'])

    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

files = ['shiro_dataset.json', 'shiro_dataset_casual.json', 'shiro_dataset_emotional.json', 'shiro_dataset_greetings.json']
for f in files:
    process_file(f)
