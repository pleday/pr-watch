# Commandes courantes ; aucune dépendance à installer (bibliothèque standard seulement).
# Everyday commands; nothing to install (standard library only).
.PHONY: help test e2e run install link

help: ## Liste des commandes / List the commands
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ —/'

test: ## Tests unitaires et d'intégration (unittest) / Unit and integration tests
	python3 -m unittest discover -t . -s tests -v

e2e: ## Test de bout en bout du tableau de bord dans un vrai Chrome (voir e2e/README.md) / End-to-end test of the dashboard in a real Chrome (see e2e/README.md)
	python3 e2e/web_setup.py && node e2e/web_e2e.mjs

run: ## Ouvre le tableau de bord web depuis les sources / Open the web dashboard from the sources
	python3 -m prwatch ui

install: ## Installe la commande `pr-watch` (pipx recommandé) / Install the `pr-watch` command (pipx recommended)
	pipx install --force .

link: ## Installe la commande `pr-watch` pour cette copie (~/.local/bin), sans pipx : les modifications du code sont prises en compte / Install `pr-watch` for this checkout (~/.local/bin), without pipx: code changes are picked up
	@mkdir -p $(HOME)/.local/bin
	@printf '#!/bin/sh\nexport PYTHONPATH=%s$${PYTHONPATH:+:$$PYTHONPATH}\nexec python3 -m prwatch "$$@"\n' "$(CURDIR)" > $(HOME)/.local/bin/pr-watch
	@chmod +x $(HOME)/.local/bin/pr-watch
	@echo "pr-watch installé / installed: $(HOME)/.local/bin/pr-watch (copie / checkout: $(CURDIR))"
