# Security Policy

This repository is a personal, read-only fork of
[RohanAnandPandit/trading212-mcp-server](https://github.com/RohanAnandPandit/trading212-mcp-server).
It is maintained on a best-effort basis with no guaranteed response time.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.3.0+readonly (this fork's `main`) | :white_check_mark: |
| Anything older | :x: |

## Reporting a Vulnerability

**Do not report security problems in public, and do not include credentials or
account data in any report.**

- For a problem in this fork, contact the fork owner through
  [their GitHub profile](https://github.com/Henry9220-bit). Issues and private
  vulnerability reporting are not enabled on this repository.
- Do not send reports about this fork to the upstream maintainer. Only if you
  have confirmed the same problem in the upstream project, report it there
  under upstream's own security policy.

A useful report includes a description of the problem, steps to reproduce it
with synthetic data, and the impact you expect.

## What matters most here

This server must never change a Trading 212 account or expose credentials. A
way to make it send any request other than a `GET` to an allowlisted read
endpoint, or to make a key or secret appear in output, logs or the cache, is a
vulnerability.

## Good practice for users

- Give the server an API key with read permissions only, restricted to your IP
  addresses where possible.
- Keep `.env` and the cache directory private. Cached responses contain account
  data in plaintext.
- Keep dependencies up to date with `uv sync --frozen` after pulling changes.
