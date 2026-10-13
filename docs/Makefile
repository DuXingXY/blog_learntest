# Sphinx 文档的最小 makefile
#

# 可从命令行设置这些变量，前两个变量也可通过环境变量设置。
SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

# 把它放在第一位，这样没有参数的 "make" 就像 "make help" 一样。
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

.PHONY: help Makefile

# 通用目标：通过 Sphinx 的新构建模式处理所有未知目标。
# $(O) 是 $(SPHINXOPTS) 的简写。
%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
