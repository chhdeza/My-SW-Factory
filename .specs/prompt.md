Help me create a harness and agents.md file that will support the creation of a self-deployable, self-healing application that I can use for whatever project I engage in. Some basic guidance is already in @.cursor/rules but I need to build a software factory that will build itself, code itself, error correct itself, review logs for failures and propose improvements by itself.

Everything automated except the deployment, where a human MUST be involved and approve.

The software factory must have its own dashboard and metrics on what it did during the day, how many fixes, commits, PR's did, estimate how many agents and tokens were used, among other interesting metrics.

Remember, this MUST be generic and a scaffold that I can export to any github repo where I want to build something.

It must use multiple specialized agents for coding, an orchestrator to avoid and resolve conflicts, quality agents on demand and security engineering agents on demand as well.

Everything must run in my machine but also be exportable to use GitHub actions runners. I have access to API Keys for LLM's.

Assume I will also be able to deploy this into AWS or GCP Free Tier so nothing should exceed those limits, but you can use all services with free tiers from there.

The software factory must also provide cron-like jobs that can be configured and executed as routines.

Use robust but open source technologies so this costs as little as possible. Use docker containers, IaC with Pulumi or OpenTofu, Kubernetes, Argo CD, etc, and any other free to use technology that I can also port to GCP or AWS.

Another requirement: The software factory must be able to review github actions to validate errors and propose fixes to those errors (part of self-healing)

Ask questions for clarification.