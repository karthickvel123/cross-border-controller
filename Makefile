.PHONY: test eval-dry lint-boundary run-api

test:
	pytest backend/tests/ -v

eval-dry:
	python -m eval.harness --dry-run --seed 20261122

lint-boundary:
	@python -c "import sys, re, glob; \
	forbidden = ['litellm', 'openai', 'anthropic', 'groq', 'google.genai', 'google.generativeai', 'llm_layer']; \
	files = glob.glob('backend/src/deterministic_core/**/*.py', recursive=True); \
	violations = []; \
	for f in files: \
	    text = open(f, encoding='utf-8').read(); \
	    for pkg in forbidden: \
	        if re.search(r'\b(import\s+' + pkg + r'|from\s+' + pkg + r')\b', text): \
	            violations.append(f'{f}: forbidden import {pkg}'); \
	if violations: \
	    print('\n'.join(violations)); \
	    sys.exit(1); \
	print('G2 Boundary Isolation: Zero forbidden LLM imports detected in deterministic_core.')"

run-api:
	uvicorn backend.src.main:app --reload --port 8000
