
# Agent PBX

## Abstract

A mcp server designed to run on localhost or in suitable lab environment over local LAN, our test agent will be Codex. Several Codex agents will be running, working on differnt projects. If the user asks the agent to use pbx, the agent will use pbx mcp functions to hit a callback when the current agent turn has completed. The callback payload should include basic details from the agent (what project, done with task, need input, plan input and proceed with plan options), for development we can can use a sqlite spool on the mcp side. There will be a client TUI app, where a terminal user from another linux system on LAN can see a global agent view. If an agent if reporting done or otherwise needs input, we should provide follow up mechanisms for centralized management, it is likely the user will need to request full output in many cases since initiual callback alerts will be summarized/brief to keep payload size down.

## High Level

### MCP server

- can run via localhost or exposed to local LAN, README should advise against exposing to internet without knowing exactly what you are doing and have prepared adequate security
- agents can
	- send updates about the response at time of return
	- request more info in general or while in plan mode workflows
	- send a detailed response when requested
- client tui can
	- view agents panel globally
	- select and agent from the tui and interact with them
		- request full details on response, usefull when complex task completed and follow up is or may be required
		- send / request / follow up / start new task / etc

### CLI TUI

- Python
- Let's make it functional and light-weight, but with a good balance of UX
	- developer experience is important to us
- The goal is to allow the developer a single pane for higher level project and agent orchestration

## TODO

Let's start with a good workable MVP, if it performs well we will move on to making the TUI experience first class.

