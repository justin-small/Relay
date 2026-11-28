.DEFAULT_GOAL := help
MINT := mint run

.PHONY: bootstrap new-app fix verify verify-github lint lint-strict lint-github lint-fix format-run format-check format-dry versions help

bootstrap: ## Install Mint + tools (Mintfile) + pre-commit hooks
	@bash scripts/bootstrap.sh

new-app: ## Scaffold a new app from this template (edit NEW_PROJECT_NAME/NEW_BUNDLE_ID in .env first)
	@bash scripts/new_app.sh

fix: format-run lint-fix ## Format + auto-fix lint (run before committing)

verify: format-check lint-strict ## Full CI gate (read-only, does not modify files)

verify-github: format-check lint-github ## CI gate for GitHub Actions (inline PR annotations)

lint: ## SwiftLint — warnings only
	@$(MINT) swiftlint lint

lint-strict: ## SwiftLint --strict — fail on any warning
	@$(MINT) swiftlint lint --strict

lint-github: ## SwiftLint --strict with GitHub Actions inline annotations
	@$(MINT) swiftlint lint --strict --reporter github-actions-logging

lint-fix: ## SwiftLint auto-correct fixable rules
	@$(MINT) swiftlint lint --fix

format-run: ## SwiftFormat — format all files
	@$(MINT) swiftformat .

format-check: ## SwiftFormat — check without modifying (for CI)
	@$(MINT) swiftformat --lint .

format-dry: ## SwiftFormat — preview changes
	@$(MINT) swiftformat --dryrun .

versions: ## Print pinned SwiftLint / SwiftFormat versions
	@$(MINT) swiftlint version
	@$(MINT) swiftformat --version

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
