import json
import os

files = [f for f in os.listdir('shiro_stack') if f.startswith('shiro_datatset_stack')]
short_thoughts = []

for filename in files:
    filepath = os.path.join('shiro_stack', filename)
    with open(filepath, 'r') as f:
        data = json.load(f)
        for i, example in enumerate(data):
            for turn in example['conversations']:
                if turn['from'] == 'gpt':
                    val = turn['value']
                    if '[THOUGHT]' in val and '[/THOUGHT]' in val:
                        thought = val.split('[THOUGHT]')[1].split('[/THOUGHT]')[0].strip()
                        word_count = len(thought.split())
                        if word_count < 20:
                            short_thoughts.append(f"{filename} Ex {i+1}: {word_count} words")

if short_thoughts:
    for s in short_thoughts:
        print(s)
else:
    print("All thoughts are 20+ words.")
