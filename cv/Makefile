.PHONY: all deps fetch render pdf clean distclean

PYTHON ?= python3

all: fetch render pdf

deps:
	$(PYTHON) -m pip install -r requirements.txt

fetch:
	$(PYTHON) scripts/fetch_orcid.py

render:
	$(PYTHON) scripts/render.py

pdf:
	pdflatex -interaction=nonstopmode -halt-on-error cv.tex
	pdflatex -interaction=nonstopmode -halt-on-error cv.tex

clean:
	rm -f cv.aux cv.log cv.out cv.fls cv.fdb_latexmk cv.synctex.gz

distclean: clean
	rm -f cv.pdf
	rm -rf .cache
