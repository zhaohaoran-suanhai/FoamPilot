---
name: openfoam-author-benchmark
description: Use when creating or revising an OpenFOAM Agent benchmark task, leakage group, evaluator contract, protocol freeze, immutable attempt policy, or aggregate report.
---

# Author an OpenFOAM Agent benchmark

## Core principle

Measure case authorship and reasoning, not tutorial retrieval. Public Agent
inputs and private evaluator assets are separate trust domains.

## Required workflow

1. Define the claim and population: Foundation version, case families, task
   count, repetitions, resource limits, Agent/model configuration, denominator,
   and success criteria.
2. Write a public task card from physical intent. It may specify geometry,
   physics, operating conditions, required observables, resource envelope, and
   allowed general documentation. It must not reveal:
   - official target path, basename, files, or distinctive dictionary content;
   - golden values, private tolerances, or validator implementation;
   - a retrieval hint that uniquely identifies the target.
3. Put official target mappings, source copies, golden generation, thresholds,
   and private validators in evaluator-only storage. The Agent adapter receives
   no path or API that can read them.
4. Assign every task a leakage group covering the whole target family, aliases,
   variants, derived summaries, and pilot-derived knowledge. Filter the group
   before retrieval, then audit the exact Agent-visible corpus.
5. Define ordered validator gates:
   isolation/compliance, case completeness, mesh, solve validity, public
   physics, and private golden agreement. Separate `FAIL_AGENT`,
   `BLOCKED_ENVIRONMENT`, `INVALID_AGENT_RUN`, and `INVALID_BENCHMARK`; only
   valid Agent-evaluable attempts enter the success denominator.
6. Before the first formal attempt, freeze the complete protocol—not only the
   selected case—including all public/private manifests, every leakage-filtered
   corpus state, knowledge and Skill versions, evaluator/golden hashes,
   environment, Agent adapter, provider/model, and resource policy. Drift
   requires a new protocol version.
7. Allocate a new exclusive attempt directory. Record public prompt, generated
   files, commands, logs, environment, hashes, observations, verdict, and
   reason codes. Never overwrite or relabel an immutable attempt.
8. Report passes, failures, blocked/invalid exclusions, gate rates, and
   not-evaluated metrics. Failed attempts remain reusable evidence.

## Release gates

- A reviewer checks that the public task is faithful but non-leaking.
- The evaluator verifies target/golden provenance and frozen hashes privately.
- A retrieval audit proves the complete leakage group is absent.
- A dry run proves classification and artifact integrity without exposing
  private values.
- Formal execution starts only after the complete freeze is stable.

## Output contract

Return:

1. public task and allowed-knowledge contract;
2. evaluator-only asset inventory and ownership;
3. leakage group and audit rule;
4. validator gates and failure taxonomy;
5. complete freeze manifest scope;
6. immutable attempt/report schema;
7. readiness verdict or exact blocker.

## Stop conditions

Refuse mounting targets in the Agent workspace, prompting with golden values,
case-by-case partial freezes, post-hoc tolerance changes, deletion of failures,
or reporting only successful attempts. Do not run a solver while authoring this
benchmark contract.
