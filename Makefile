# Optional per §7.1 -- Python needs no build step. This exists purely to
# save retyping the same commands over the next month, not for grading.

PYTHON  ?= python3
HOST    ?= 127.0.0.1
PORT    ?= 5000

.PHONY: test run trader md clean structure zip

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -u src/server.py $(HOST) $(PORT)

# make trader USER=alice
trader:
	$(PYTHON) -u src/trader.py $(HOST) $(PORT) $(USER)

# make md INSTR=JNST
md:
	$(PYTHON) -u src/market_data.py $(HOST) $(PORT) $(INSTR)

structure:
	sh src/tools/check_structure.sh

# make zip R1=2024CS10388 R2=2024AM10224
zip:
	sh src/tools/make_zip.sh $(R1) $(R2)

clean:
	find . -name '__pycache__' -type d -exec rm -rf {} +
	find . -name '.DS_Store' -delete
	rm -rf .pytest_cache
