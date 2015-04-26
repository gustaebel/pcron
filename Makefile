.PHONY: all doc clean test-long test-short

all: doc test

doc:
	make -C doc

clean:
	make -C doc clean

test-all: test-long test-short

test: test-short

test-long:
	@echo "============================================"
	@echo " ATTENTION! This test takes over 1h to run!"
	@echo "============================================"
	@echo
	python3 -m unittest -v tests.test_realtime

test-short:
	python3 -m unittest -v tests.test_pcron
