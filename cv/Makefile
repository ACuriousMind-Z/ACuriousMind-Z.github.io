.PHONY: all deps fetch profile render pdf clean distclean

PYTHON ?= python3

all: fetch profile render pdf

deps:
	$(PYTHON) -m pip install -r requirements.txt

fetch:
	$(PYTHON) scripts/fetch_orcid.py

profile:
	$(PYTHON) scripts/fetch_orcid_profile.py

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
