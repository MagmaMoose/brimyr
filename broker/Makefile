# The whole local loop for the brimyr token broker.
#
#   make up      start LocalStack
#   make zip     build the Lambda package (the same script CI publishes with)
#   make apply   terraform apply against LocalStack
#   make seed    put a throwaway App identity into SSM
#   make smoke   drive the deployed API end to end
#   make all     up -> zip -> apply -> seed -> smoke
#   make down    stop LocalStack and drop the volume
#
# `make all` from a clean machine is the whole story. Nothing here touches real AWS: the
# provider in localstack/provider.tf points every endpoint at :4566 and the credentials
# are LocalStack's documented fakes.

SHELL := /usr/bin/env bash
LOCALSTACK := localstack
# OpenTofu if present, else terraform. Not interchangeable by accident here: the module
# declares required_version >= 1.9 and Homebrew's `terraform` formula is pinned at 1.5.7
# (the last MPL release), so a bare `terraform` on a dev machine fails the version
# constraint. Override with `make TF=terraform ...` if you have a new enough one.
TF := $(shell command -v tofu 2>/dev/null || command -v terraform)
ZIP := $(CURDIR)/dist/broker.zip
# The Lambda runtime target. NOT the build machine's platform -- cryptography ships
# compiled wheels and a macOS ARM build would ImportError inside Lambda.
PLATFORM := x86_64-manylinux_2_28

.PHONY: all up down zip apply seed smoke test fmt clean

all: up zip apply seed smoke

up:
	docker compose -f $(LOCALSTACK)/docker-compose.yml up -d --wait
	@echo "LocalStack ready on :4566"

down:
	docker compose -f $(LOCALSTACK)/docker-compose.yml down -v

zip:
	@mkdir -p $(dir $(ZIP))
	uv run --extra dev python scripts/build_lambda_zip.py --out $(ZIP) --platform $(PLATFORM)

apply: zip
	cd $(LOCALSTACK) && $(TF) init -input=false >/dev/null && \
	  $(TF) apply -auto-approve -input=false -var="lambda_zip_path=$(ZIP)"

seed:
	$(LOCALSTACK)/seed.sh /brimyr/local

smoke:
	@cd $(LOCALSTACK) && ./smoke.py "$$($(TF) output -raw api_endpoint)"

test:
	uv run --extra dev pytest -q

fmt:
	uv run --extra dev ruff check --fix . && uv run --extra dev ruff format .
	$(TF) fmt -recursive .

clean:
	rm -rf dist $(LOCALSTACK)/.terraform $(LOCALSTACK)/terraform.tfstate*
