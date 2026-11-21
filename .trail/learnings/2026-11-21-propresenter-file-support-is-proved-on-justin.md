---
title: "ProPresenter file support is proved on the real install first, because version 22 may not read loose .pro files"
author: Justin
date: 2026-11-21
branch: propresenter-files-497
cost: { minutes: 3, tokens: 57663 }
id: l-8a6f678f
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-21, issue 497: before building on the Pro7 file format, write a test song into the real ProPresenter library and confirm the app shows it. Stop and report if it does not. Vendor the MIT protos and use the protobuf package; refuse writes if the process is running or the network API answers; one PR for the whole issue.

## Tried and ruled out
Building to the Pro7 format and testing only with fixtures: not chosen, it would not show whether a current install reads the files.
Hand-written minimal decoder: not chosen, more fragile across versions.

## Watch out
The Mac has ProPresenter 22, which also keeps a workspace database, so loose .pro files may not be what it reads. The latest protos (autogen-proto branch) do decode a 22 .pro file.
In the code: library is ~/Library/Application Support/RenewedVision/ProPresenter/UserWorkspaces/ProPresenter/Libraries/Default/; protoc comes from grpcio-tools in a scratch venv, not the project venv.
