# Minimal Sphinx makefile.
#
#     make html      build the site into _build/html
#     make strict    build it the way CI does, with warnings as errors
#     make clean     throw the build away

SPHINXBUILD   ?= python -m sphinx
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: help html strict clean

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)"

html:
	@$(SPHINXBUILD) -b html -d "$(BUILDDIR)/doctrees" "$(SOURCEDIR)" "$(BUILDDIR)/html"

strict:
	@$(SPHINXBUILD) -W --keep-going -b html -d "$(BUILDDIR)/doctrees" "$(SOURCEDIR)" "$(BUILDDIR)/html"

clean:
	@$(SPHINXBUILD) -M clean "$(SOURCEDIR)" "$(BUILDDIR)"
