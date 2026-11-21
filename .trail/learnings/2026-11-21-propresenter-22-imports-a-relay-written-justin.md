---
title: "ProPresenter 22 imports a Relay-written song when the file is opened with the app, no dialog; its library is a database, not loose files"
author: Justin
date: 2026-11-21
branch: propresenter-files-497
cost: { minutes: 4, tokens: 128547 }
id: l-821d20a2
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-21, issue 497: he does not want to import by hand. Relay writes .pro files with the vendored protos and hands them to ProPresenter by opening them with the app, which imports them into the workspace.

## Tried and ruled out
Dropping .pro files into the old UserWorkspaces library folder: ProPresenter 22 ignores them.
File > Import > File by hand: works but is manual.
Writing the workspace database directly: not needed; it is sqlite with Loro CRDT snapshots per document and would be fragile.

## Watch out
In the code: `open -a ProPresenter <file>.pro` imported a song into Default with no prompt (macOS, 22.9). Workspace is Workspaces/<uuid>/DocumentStore/workspace-v1.sqlite3, table workspace_streams (kind Presentation, LibraryData, PlaylistLibrary), snapshots start with "loro". Playlist, look, macro and Bible import paths are not tested yet. Only works when Relay runs on the same Mac as ProPresenter.
