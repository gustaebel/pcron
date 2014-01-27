# txt2man can be found at http://freshmeat.net/projects/txt2man/

RELEASE := pcron $(shell python ../setup.py -V)

.PHONY: manpages clean

all: manpages

manpages: pcron.1 pcrond.1 pcrontab.1 pcrontab.5

pcron.1: pcron.man ../libpcron/__init__.py
	txt2man -s 1 -t pcron -r "$(RELEASE)" pcron.man 2>&1 > pcron.1

pcrond.1: pcrond.man ../libpcron/__init__.py
	txt2man -s 1 -t pcrond -r "$(RELEASE)" pcrond.man 2>&1 > pcrond.1

pcrontab.1: pcrontab1.man ../libpcron/__init__.py
	txt2man -s 1 -t pcrontab -r "$(RELEASE)" pcrontab1.man 2>&1 > pcrontab.1

pcrontab.5: pcrontab5.man ../libpcron/__init__.py
	txt2man -s 5 -t pcrontab -r "$(RELEASE)" pcrontab5.man 2>&1 > pcrontab.5

clean:
	$(RM) *.1 *.5
