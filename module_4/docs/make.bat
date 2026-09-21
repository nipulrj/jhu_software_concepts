@ECHO OFF
REM Minimal Sphinx build script for Windows.
REM
REM     make html      build the site into _build\html
REM     make strict    build it the way CI does, with warnings as errors
REM     make clean     throw the build away

pushd %~dp0

set SPHINXBUILD=python -m sphinx
set SOURCEDIR=.
set BUILDDIR=_build

if "%1" == "" goto help
if "%1" == "html" goto html
if "%1" == "strict" goto strict

%SPHINXBUILD% -M %1 %SOURCEDIR% %BUILDDIR%
goto end

:help
%SPHINXBUILD% -M help %SOURCEDIR% %BUILDDIR%
goto end

:html
%SPHINXBUILD% -b html -d %BUILDDIR%\doctrees %SOURCEDIR% %BUILDDIR%\html
goto end

:strict
%SPHINXBUILD% -W --keep-going -b html -d %BUILDDIR%\doctrees %SOURCEDIR% %BUILDDIR%\html
goto end

:end
popd
