from axiom_files.validator import validate_file
agents = ['game_watcher', 'pattern_agent', 'skill_builder', 'controller_mapper']
icons = {'valid': 'OK', 'warning': 'WARN', 'invalid': 'FAIL'}
for agent in agents:
    r = validate_file(agent)
    status = r['status'].upper()
    icon = icons.get(r['status'], '?')
    print(f"\n[{icon}] {agent.upper()}.axiom -- {status}")
    if r['issues']:
        for i in r['issues']:
            print(f"   [{i['level'].upper()}] [{i['phase']}] {i['field']}: {i['message'][:100]}")
    else:
        print("   No issues.")
    if r['suggestions']:
        for s in r['suggestions']:
            print(f"   -> {s[:100]}")
