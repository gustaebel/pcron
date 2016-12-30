# txt2man can be found at http://freshmeat.net/projects/txt2man/

RELEASE := pcron $(shell python ../setup.py -V)

.PHONY: manpages clean

all: manpages

sources := $(basename $(wildcard *.man))

manpages: $(sources)

%.1: %.1.man ../libpcron/__init__.py
	$(txt2man)

%.5: %.5.man ../libpcron/__init__.py
	$(txt2man)

%.7: %.7.man ../libpcron/__init__.py
	$(txt2man)

define txt2man =
txt2man -s $(subst .,,$(suffix $(suffix $@))) -t $(basename $(basename $<)) -r "$(RELEASE)" $< > $@
endef

clean:
	$(RM) *.1 *.5 *.7
