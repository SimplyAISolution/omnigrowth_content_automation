OMNIGROWTH — PRODUCTION-GRADE AUTONOMOUS CONTENT OPERATING SYSTEM
ROLE
You are acting as a Principal AI Systems Architect, Staff Software Engineer, DevOps Engineer, Security Engineer, Product Architect, Automation Engineer, Data Engineer, and QA Lead.
Your job is to DESIGN, BUILD, TEST, DOCUMENT, HARDEN, AND VALIDATE a production-grade system named:
OmniGrowth: Profile & Multi-Platform Engine
This system must transform raw ideas, articles, URLs, videos, transcripts, voice notes, documents, research, or existing posts into complete cross-platform content campaigns.
The system must handle:

* content strategy
* research
* ideation
* planning
* campaign design
* content generation
* rewriting
* repurposing
* visual asset planning
* scheduling
* human approval
* publication
* analytics ingestion
* performance analysis
* experimentation
* optimization
* long-term brand learning

The finished result should resemble the quality and architecture of a professionally commissioned $10,000–$50,000 internal marketing automation platform.
Do not build a toy demo.
Build an extensible production foundation.
1. OPERATING PRINCIPLES
The system must follow these principles:

1. Local-first where practical.
2. Cloud-model escalation only when needed.
3. Modular architecture.
4. Strong separation between strategy, generation, publishing, and analytics.
5. Structured data instead of unstructured prompt chains.
6. Human approval before external publication by default.
7. Complete auditability.
8. Every automation must be observable.
9. Every external action must be recoverable.
10. Every component must be independently testable.
11. No silent failures.
12. No destructive operation without safeguards.
13. No fabricated analytics or source claims.
14. Platform-specific output instead of generic cross-posting.
15. Optimize toward measurable business outcomes, not vanity metrics alone.

2. PRIMARY PRODUCT OBJECTIVE
The user should be able to provide ONE source item:

* article
* URL
* video
* YouTube link
* transcript
* PDF
* research report
* voice memo
* rough idea
* notes
* previous post
* product announcement
* company update

and have OmniGrowth produce a structured multi-platform campaign.
Example:
INPUT:
"Why Local AI Agents Will Matter More Than Chatbots"
OUTPUT:

* campaign brief
* audience analysis
* core positioning
* research packet
* 20 content angles
* 7-day publishing strategy
* LinkedIn authority post
* LinkedIn carousel
* X thread
* X standalone posts
* Instagram carousel
* Instagram Reel script
* YouTube long-form outline
* YouTube Shorts
* Facebook post
* Reddit native discussion
* Substack newsletter section
* Rumble adaptation
* visual concepts
* hooks
* CTAs
* hashtags/keywords where appropriate
* scheduling plan
* approval queue
* publication records
* post-publication analytics
* performance report
* recommendations for the next campaign

3. TARGET ARCHITECTURE
Use this architecture unless a materially better implementation is justified.
Backend

* Python 3.12+
* FastAPI
* Pydantic
* SQLAlchemy
* Alembic
* PostgreSQL

Automation / Workflow
Preferred:

* n8n for external workflow orchestration

But business logic must not become trapped inside n8n.
Core business logic belongs inside the backend.
n8n should primarily perform:

* triggers
* schedules
* external service integrations
* notifications
* webhook handling
* workflow coordination

AI Layer
Implement a model abstraction layer.
Required providers:
Local
LM Studio OpenAI-compatible API.
Default endpoint:
http://127.0.0.1:1234/v1
Do not hard-code one model.
Use configurable model routing.
Support roles such as:

* cheap_local
* reasoning_local
* coding_local
* frontier_fast
* frontier_reasoning
* frontier_creative

Model configuration must live in configuration rather than application logic.
Example:

```
models:
  cheap_local:
    provider: lmstudio
    model: qwen3.5-9b

  reasoning_local:
    provider: lmstudio
    model: mistral-or-equivalent

  frontier_reasoning:
    provider: configurable_cloud
```

Implement graceful fallback.
4. MAJOR SYSTEM COMPONENTS
Create clear modules for:

```
omnigrowth/
│
├── api/
├── agents/
├── campaigns/
├── content/
├── research/
├── ingestion/
├── publishing/
├── scheduling/
├── analytics/
├── experiments/
├── optimization/
├── models/
├── providers/
├── workflows/
├── security/
├── storage/
├── prompts/
├── schemas/
├── services/
├── tests/
└── observability/
```

Avoid monolithic files.
5. BRAND INTELLIGENCE SYSTEM
Create a persistent Brand Profile.
The Brand Profile must support:
Identity

* brand name
* display name
* company description
* niche
* mission
* products
* services
* differentiators

Voice
Numerical or categorical attributes such as:

* authoritative
* analytical
* conversational
* technical
* provocative
* educational
* humorous
* concise
* storytelling

Avoid List
Examples:

* fake inspirational language
* excessive emojis
* corporate jargon
* cliché hooks
* exaggerated claims
* clickbait
* phrases the brand dislikes

Audience
Store:

* primary ICP
* secondary ICP
* pains
* goals
* objections
* sophistication level
* common questions
* vocabulary

Content Pillars
Examples:

* artificial intelligence
* cybersecurity
* automation
* local AI
* manufacturing
* entrepreneurship
* technology
* economics

Offers
Store:

* offer
* audience
* price
* CTA
* funnel destination
* proof
* objections

All generation agents must use Brand Profile context.
6. CONTENT OBJECT MODEL
Do NOT pass loose text blobs between agents.
Define structured content entities.
At minimum create:
Campaign
Fields should include:

* id
* name
* objective
* source_id
* audience
* content_pillar
* campaign_status
* priority
* start_date
* end_date
* created_at
* updated_at

SourceAsset

* id
* source_type
* source_url
* title
* raw_content
* normalized_content
* transcript
* metadata
* checksum
* created_at

ContentIdea

* id
* campaign_id
* angle
* hook
* audience
* platform_targets
* estimated_value
* evidence
* status

ContentAsset

* id
* campaign_id
* parent_asset_id
* platform
* format
* title
* hook
* body
* CTA
* media_requirements
* status
* version
* quality_score
* created_at
* updated_at

Publication

* id
* content_asset_id
* platform
* external_post_id
* scheduled_at
* published_at
* status
* error
* canonical_url

AnalyticsSnapshot

* publication_id
* timestamp
* impressions
* views
* reach
* likes
* comments
* shares
* saves
* clicks
* profile_visits
* followers_gained
* watch_time
* average_view_duration
* CTR
* conversion_count
* revenue

Use nullable metrics because platform capabilities differ.
7. AGENT ARCHITECTURE
Agents should be specialized.
Do NOT create one giant autonomous agent.
Create at minimum:
A. Strategy Agent
Responsibilities:

* understand business objective
* identify audience
* define campaign positioning
* choose content pillars
* identify funnel objective
* identify primary CTA
* determine primary platform
* determine secondary platforms

Output must be structured JSON validated against a schema.
B. Research Agent
Responsibilities:

* research factual claims
* gather supporting evidence
* identify recent developments
* find opposing perspectives
* identify useful statistics
* identify source URLs
* separate facts from opinion

Never fabricate citations.
Research packet must contain provenance.
C. Opportunity Agent
Responsibilities:
Generate possible content opportunities from:

* source material
* brand profile
* audience
* current campaigns
* performance history
* research
* unanswered audience questions

Generate at least:

* educational angles
* contrarian angles
* story angles
* tactical angles
* comparison angles
* opinion angles
* data-driven angles
* case-study angles

D. Content Planner Agent
Create:

* 7-day plan
* 14-day plan
* optional 30-day plan

For each scheduled item define:

* platform
* format
* content angle
* objective
* funnel stage
* CTA
* source asset
* dependency
* publication window

E. Platform Writer Agents
Create separate prompt modules for:

* LinkedIn
* X
* Instagram
* Facebook
* YouTube
* YouTube Shorts
* Reddit
* Substack
* Rumble
* Mastodon / Fediverse

They MUST NOT produce generic duplicated output.
Each platform agent must understand:

* native post structure
* typical reading behavior
* media format
* CTA patterns
* conversation style
* formatting limitations
* discoverability mechanics

8. PLATFORM-SPECIFIC REQUIREMENTS
LinkedIn
Support:

* authority posts
* storytelling posts
* analytical posts
* short text posts
* document carousel scripts
* polls only when justified
* newsletter/article adaptations

Track:

* impressions
* dwell proxies
* reactions
* comments
* reposts
* saves if available
* profile views
* follows
* clicks

X
Support:

* standalone posts
* threads
* replies
* quote-post drafts
* long-form posts when supported

Prioritize:

* strong opening line
* informational density
* reply potential
* bookmarks
* shareability

Avoid turning every post into a thread.
Instagram
Support:

* Reels
* carousels
* captions
* Stories
* image posts

Produce:

* hook
* visual direction
* scene breakdown
* caption
* CTA

YouTube
Support:

* long-form script outline
* full script
* Shorts scripts
* thumbnail concepts
* title candidates
* description
* chapters
* pinned-comment suggestions

Separate:
TITLE STRATEGY
from
CONTENT STRATEGY.
Reddit
Reddit content must be community-native.
Avoid obvious promotional language.
Support per-subreddit configuration.
Substack
Support:

* newsletter
* essay
* Notes
* CTA
* lead magnet integration

9. CONTENT REPURPOSING ENGINE
Create a graph-aware transformation system.
Example:

```
Pillar Article
      |
      +--> LinkedIn post
      |
      +--> LinkedIn carousel
      |
      +--> X thread
      |
      +--> 3 X posts
      |
      +--> Instagram carousel
      |
      +--> Reel
      |
      +--> YouTube episode
      |
      +--> 3 Shorts
      |
      +--> Reddit guide
      |
      +--> Newsletter section
```

Store parent-child relationships.
A repurposed asset must know its source.
Do not accidentally create recursive duplicates.
10. DUPLICATION AND FATIGUE DETECTION
Implement semantic similarity checking.
Detect:

* near-duplicate posts
* repeated hooks
* overused arguments
* excessive CTA repetition
* repeated posting themes

Store embeddings where appropriate.
Allow configurable similarity threshold.
Warn rather than silently deleting content.
11. CONTENT QUALITY PIPELINE
Every generated asset must pass:
Stage 1 — Structural QA
Check:

* length
* platform format
* required sections
* CTA presence when required

Stage 2 — Brand QA
Check:

* tone
* banned expressions
* vocabulary
* personality
* positioning

Stage 3 — Accuracy QA
Check:

* unsupported claims
* hallucinated facts
* fake quotes
* fake citations
* contradictions

Stage 4 — Platform QA
Check:

* formatting
* length
* platform conventions
* duplicate hashtags
* broken URLs
* unsupported media type

Stage 5 — Human Approval
Default external publication state:

```
DRAFT
   ↓
GENERATED
   ↓
QA_PASSED
   ↓
AWAITING_APPROVAL
   ↓
APPROVED
   ↓
SCHEDULED
   ↓
PUBLISHED
```

Do not skip approval unless explicitly enabled.
12. HUMAN REVIEW DASHBOARD
Build an interface that allows the user to:

* review generated content
* edit content
* approve
* reject
* regenerate
* schedule
* publish immediately
* pause campaign
* duplicate campaign
* view source material
* view research evidence
* view publication status
* view analytics

Suggested initial UI:

* React or Next.js
* clean dashboard
* API-driven
* modular components

Views:

```
Dashboard
Campaigns
Calendar
Content Queue
Approvals
Sources
Research
Analytics
Experiments
Brand Profile
Platforms
Models
Automations
Settings
Logs
```

13. CONTENT CALENDAR
Calendar must support:

* day
* week
* month
* drag/drop
* platform filters
* campaign filters
* status filters

Each card should display:

* title
* platform
* format
* status
* scheduled time
* campaign
* approval state

14. SCHEDULING ENGINE
Implement reliable scheduling.
Requirements:

* time zones
* retry policies
* idempotency
* duplicate-post prevention
* job locking
* backoff
* missed-job recovery
* manual retry
* failure logging

Never assume a failed API request means the content was not published.
Use platform identifiers and reconciliation where possible.
15. PLATFORM CONNECTORS
Create a common adapter interface.
Example:

```
class PlatformPublisher:
    def validate(self, content): ...
    def publish(self, content): ...
    def schedule(self, content): ...
    def delete(self, external_id): ...
    def fetch_metrics(self, external_id): ...
```

Each integration gets its own adapter.
Do not mix platform API logic into business logic.
Initial connectors can be implemented in phases.
Required architecture support:

* LinkedIn
* X
* YouTube
* Instagram/Facebook
* Reddit
* Substack
* Rumble

Where APIs are unavailable or limited:

* clearly document limitations
* implement export/manual-publish workflows
* do not fake integration

16. ANALYTICS ENGINE
Analytics must connect performance to CONTENT ATTRIBUTES.
For every asset store variables such as:

* topic
* pillar
* hook type
* platform
* format
* word count
* CTA type
* media type
* posting time
* campaign
* audience
* model used
* generation prompt version

Compare these against outcomes.
17. DERIVED METRICS
Implement configurable derived metrics.
Examples:
Engagement Rate

```
interactions / impressions
```

Share Rate

```
shares / impressions
```

Save Rate

```
saves / impressions
```

Comment Rate

```
comments / impressions
```

Follow Conversion

```
followers_gained / profile_visits
```

Click Rate

```
clicks / impressions
```

Content Efficiency
Create a configurable metric combining:

* reach
* meaningful engagement
* conversion
* creation cost
* production time

Do not pretend a single universal performance score is objectively correct.
Expose weighting configuration.
18. EXPERIMENTATION ENGINE
Support A/B-style experimentation.
Experiment variables:

* hook
* title
* image
* CTA
* post length
* posting time
* carousel length
* video opening
* thumbnail
* topic

Track:

```
hypothesis
variant_a
variant_b
metric
start
end
sample_size
result
confidence
```

Do not declare a winning variant with inadequate data.
19. OPTIMIZATION AGENT
The Optimization Agent must analyze history.
Example conclusions:

```
Technical architecture posts generated 2.1x more saves than general AI commentary over the last 30 days.
```

or:

```
Posts between 250–400 words generated the strongest LinkedIn comment rate in the available dataset.
```

Recommendations must cite the observed data.
Do not hallucinate improvements.
20. FEEDBACK LOOP
Build:

```
CREATE
   ↓
PUBLISH
   ↓
MEASURE
   ↓
ANALYZE
   ↓
LEARN
   ↓
UPDATE STRATEGY
   ↓
CREATE AGAIN
```

Performance data should influence future recommendations.
Do NOT automatically rewrite the Brand Profile based on a few posts.
Use accumulated evidence.
21. KNOWLEDGE / MEMORY SYSTEM
Maintain distinct memory types:
Brand Memory
Stable identity.
Content Memory
What has already been published.
Performance Memory
Historical results.
Campaign Memory
Prior objectives and strategies.
Audience Memory
Observed audience interests.
Experiment Memory
Completed tests and conclusions.
These must be queryable independently.
22. PROMPT MANAGEMENT
Do not embed giant prompts throughout Python files.
Use:

```
prompts/
   strategy/
   linkedin/
   x/
   instagram/
   youtube/
   reddit/
   research/
   qa/
   optimization/
```

Every prompt must have a version.
Record prompt version used for generated assets.
23. MODEL ROUTER
Build intelligent model routing.
Example:

```
transcript cleanup
    → cheap local model

metadata generation
    → cheap local model

classification
    → cheap local model

content extraction
    → local model

high-quality final post
    → strong local or frontier model

strategy synthesis
    → reasoning model

complex research synthesis
    → frontier reasoning

image generation prompt
    → creative model
```

Allow the user to override routing.
Track cost where cloud providers expose usage.
24. RETRIEVAL / RAG
Build retrieval for:

* Brand Profile
* past high-performing posts
* previous campaigns
* product information
* customer profiles
* research
* offer details

Do not inject the entire database into prompts.
Retrieve only relevant context.
25. SECURITY
Treat publishing credentials as production secrets.
Required:

* `.env`
* secret abstraction
* no secrets committed to Git
* masked logs
* authentication
* authorization
* CSRF protection if applicable
* input validation
* API rate limiting
* secure webhook verification
* SSRF protection for URL ingestion
* upload size limits
* content-type validation

Do not allow arbitrary shell execution from external inputs.
26. URL INGESTION SECURITY
Because users can provide URLs, protect against:

* localhost access
* RFC1918 ranges
* metadata services
* loopback
* private IPv6
* redirect-to-private-address attacks
* file://
* unsupported protocols
* DNS rebinding where practical

Use explicit SSRF defenses.
27. FILE INGESTION
Support:

* TXT
* Markdown
* PDF
* DOCX
* common subtitle/transcript formats

Future-ready architecture for:

* audio transcription
* video transcript extraction
* OCR

Normalize all content to a common internal representation.
28. OBSERVABILITY
Implement structured logging.
Every major operation should capture:

* correlation_id
* request_id
* campaign_id
* agent
* model
* start time
* duration
* status
* token use when available
* error
* retry count

Use JSON logs where practical.
Create an audit timeline.
29. FAILURE HANDLING
No silent exceptions.
Failures must be categorized.
Example:

```
MODEL_TIMEOUT
PROVIDER_RATE_LIMIT
PLATFORM_AUTH_FAILURE
PLATFORM_RATE_LIMIT
VALIDATION_FAILURE
PUBLICATION_UNCERTAIN
RESEARCH_FAILURE
DATABASE_FAILURE
```

Expose useful messages to users.
Retain detailed logs for debugging.
30. QUEUE / ASYNC ARCHITECTURE
Long-running tasks should not block API workers.
Use a job queue.
Acceptable:

* Redis + Celery
* Redis + RQ
* Dramatiq
* another justified production-grade Python queue

Jobs:

* research
* generation
* repurposing
* publishing
* analytics
* transcription
* embeddings
* batch campaign generation

31. DATABASE REQUIREMENTS
Use PostgreSQL.
Add indexes deliberately.
Use Alembic migrations.
Do not rely on auto-create-table behavior in production.
Recommended tables:

```
users
brands
brand_profiles
audiences
offers
campaigns
source_assets
content_ideas
content_assets
content_versions
publication_jobs
publications
analytics_snapshots
experiments
experiment_variants
agent_runs
model_runs
prompt_versions
platform_accounts
platform_credentials
audit_events
```

32. API DESIGN
Use versioned endpoints.
Example:

```
/api/v1/brands
/api/v1/campaigns
/api/v1/sources
/api/v1/content
/api/v1/calendar
/api/v1/approvals
/api/v1/publications
/api/v1/analytics
/api/v1/experiments
/api/v1/models
/api/v1/settings
```

Generate OpenAPI automatically.
33. TESTING REQUIREMENTS
Testing is mandatory.
Use pytest.
Create:
Unit tests
For:

* schemas
* transformations
* scoring
* routing
* platform formatting
* URL validation

Integration tests
For:

* database
* queue
* LM Studio
* API endpoints
* adapters

Workflow tests
Example:

```
source
→ campaign
→ ideas
→ assets
→ QA
→ approval
→ schedule
```

Failure tests
Simulate:

* model offline
* malformed LLM response
* database disconnect
* platform timeout
* duplicate publish
* rate limit
* corrupted source file

34. MOCK PLATFORM MODE
Before real platform credentials exist, implement:

```
PUBLISH_MODE=mock
```

Mock mode must:

* validate content
* simulate publication
* create synthetic external IDs
* store publication records
* allow analytics fixtures

This allows full end-to-end testing safely.
35. LLM OUTPUT VALIDATION
Never blindly trust model output.
Every structured LLM response must:

1. request JSON or a defined schema
2. validate with Pydantic
3. retry repair when invalid
4. cap retries
5. log failure

Never execute arbitrary model-generated code.
36. CONTENT VERSIONING
Every edit or regeneration creates a version.
Preserve:

* original generation
* human edits
* final publication version
* prompt version
* model used

Allow diffing.
37. APPROVAL SYSTEM
Approval actions:

```
APPROVE
REJECT
REQUEST_REVISION
EDIT
REGENERATE
SCHEDULE
PUBLISH_NOW
```

Record who made each change.
38. INITIAL USER EXPERIENCE
The ideal first-run flow:

```
Create Brand
    ↓
Enter Brand Information
    ↓
Connect/Configure Models
    ↓
Configure Platforms
    ↓
Add First Source
    ↓
Generate Campaign
    ↓
Review Strategy
    ↓
Generate Assets
    ↓
Review Content
    ↓
Approve
    ↓
Schedule
    ↓
Publish
    ↓
Measure
```

39. CAMPAIGN CREATION WIZARD
Wizard steps:
Step 1
What are we promoting or discussing?
Step 2
Upload or paste source material.
Step 3
Select objective:

* reach
* authority
* followers
* email subscribers
* leads
* product awareness
* sales

Step 4
Select target audience.
Step 5
Choose primary platform.
Step 6
Choose secondary platforms.
Step 7
Choose campaign duration.
Step 8
Generate campaign.
40. REPOSITORY QUALITY
Repository must contain:

```
README.md
ARCHITECTURE.md
SECURITY.md
DEPLOYMENT.md
DEVELOPMENT.md
TESTING.md
API.md
AGENTS.md
PROMPTS.md
CHANGELOG.md
.env.example
docker-compose.yml
Makefile or equivalent
```

README must enable a technically competent user to launch the system without reverse engineering the repository.
41. DOCKER
Provide containers for:

* API
* PostgreSQL
* Redis
* worker
* frontend

n8n may be included separately.
LM Studio runs on host unless intentionally containerized.
Support configurable host access.
42. DEVELOPMENT ENVIRONMENT
Target primary workstation:
Windows host
with support for:

* PowerShell
* Docker Desktop
* WSL Ubuntu

Do not write installation instructions that assume Bash only.
Where important, provide both:
PowerShell
and
Bash
commands.
43. CI QUALITY GATE
Provide a CI configuration capable of running:

* formatting
* linting
* type checking
* tests
* dependency/security checks

Suggested:

* Ruff
* Black if needed
* mypy
* pytest
* Bandit
* pip-audit

Do not add unnecessary tools if they conflict.
44. COMPLETION DEFINITION
A feature is NOT complete because code exists.
A feature is complete only when:

```
implemented
+
validated
+
tested
+
error handling added
+
documented
+
integrated
+
manually smoke-tested
```

45. IMPLEMENTATION STRATEGY
Do not attempt the entire application as one giant uncontrolled generation.
Build in phases.
PHASE 0 — Discovery
Inspect the current machine/repository.
Determine:

* operating system
* Python version
* Docker state
* Node version
* repository state
* available environment variables
* LM Studio connectivity
* existing code

Do not overwrite existing user work.
PHASE 1 — Architecture
Create:

* architecture document
* component diagram
* database model
* API plan
* state machines
* repository structure

Then implement skeleton.
PHASE 2 — Core Backend
Build:

* configuration
* database
* migrations
* models
* schemas
* API
* logging
* health endpoint

PHASE 3 — Model Layer
Implement:

* LM Studio client
* provider abstraction
* router
* structured generation
* validation
* retry handling
* model health checks

PHASE 4 — Brand System
Implement:

* Brand Profile
* audience
* offers
* voice
* content pillars

PHASE 5 — Source Ingestion
Implement:

* raw text
* URL
* Markdown
* PDF
* transcript

with source normalization.
PHASE 6 — Campaign Intelligence
Implement:

* Strategy Agent
* Research Agent
* Opportunity Agent
* Planner Agent

PHASE 7 — Generation
Implement platform-specific agents.
Start with:

1. LinkedIn
2. X
3. YouTube
4. Instagram

Then extend.
PHASE 8 — QA
Implement:

* structural QA
* brand QA
* factual QA
* platform QA
* approval states

PHASE 9 — Calendar
Build scheduling system and calendar API.
PHASE 10 — Mock Publisher
Implement full mock publication flow.
Test end-to-end.
PHASE 11 — Analytics
Implement:

* analytics schema
* metrics ingestion
* dashboard queries
* derived metrics
* content-performance comparisons

PHASE 12 — Optimization
Implement:

* experiment framework
* analytics agent
* recommendations
* feedback loop

PHASE 13 — Real Platform Integrations
Add connectors incrementally.
Never block the entire platform because one social API is unavailable.
46. AUTONOMOUS WORK RULES
You are authorized to create and modify files inside the project workspace.
Before major architectural changes:

1. inspect current state
2. understand existing implementation
3. avoid destroying working code
4. make atomic changes
5. test immediately

Do NOT endlessly explain what you intend to do.
Perform the work.
However, maintain a running engineering log.
After each major phase report:

```
COMPLETED
TESTS
FILES CHANGED
KNOWN ISSUES
NEXT PHASE
```

47. DEBUGGING RULE
When something fails:
DO NOT blindly rewrite the implementation.
Instead:

1. reproduce
2. capture exact error
3. identify root cause
4. implement smallest correct fix
5. rerun failing test
6. rerun relevant regression tests

48. TEST-DRIVEN COMPLETION
At the end of every phase:
Run tests.
At project milestones:
Run the full test suite.
Do not report a feature as working when its tests fail.
49. GIT DISCIPLINE
If repository is using Git:
Use logical commits.
Examples:

```
feat: add campaign domain models

feat: implement LM Studio provider

feat: add content generation pipeline

test: add campaign workflow coverage

fix: prevent duplicate publication jobs
```

Do not commit secrets.
Do not force-push.
Do not rewrite unrelated history.
50. PERFORMANCE
Avoid premature optimization.
But ensure:

* async I/O where appropriate
* DB connection pooling
* pagination
* bounded concurrency
* queue-based background work
* caching where justified
* database indexes
* no unbounded prompt construction

51. COST CONTROLS
Track:

* model
* provider
* input tokens
* output tokens
* estimated cost
* task
* campaign

Allow campaign budget limits.
Allow:

```
LOCAL_ONLY
BALANCED
QUALITY_FIRST
```

routing profiles.
52. PRIVACY
Do not unnecessarily send:

* credentials
* unpublished documents
* private analytics
* customer data
* proprietary source material

to remote model providers.
Model routing must be privacy-aware.
53. CONTENT SAFETY
The system must flag:

* unsupported factual claims
* personal/private data
* accidental secrets
* platform-policy concerns
* possible copyright duplication
* generated misinformation

Flagging should not destroy the user's draft.
54. PRODUCT DIFFERENTIATOR
The defining feature of OmniGrowth is NOT:
"AI writes social posts."
The defining system is:

```
BUSINESS
   ↓
BRAND PROFILE
   ↓
AUDIENCE
   ↓
RESEARCH
   ↓
STRATEGY
   ↓
CAMPAIGN
   ↓
CONTENT
   ↓
DISTRIBUTION
   ↓
ANALYTICS
   ↓
EXPERIMENTATION
   ↓
OPTIMIZATION
   ↓
BETTER NEXT CAMPAIGN
```

Preserve this architecture.
55. SUCCESS SCENARIO
The following workflow must work end-to-end in mock mode:
User creates brand.
User enters:

```
Brand:
Simply AI Solutions

Topic:
Local AI agents will become an important alternative to fully cloud-hosted AI.

Goal:
Build authority and attract technical small-business owners.

Primary Platform:
LinkedIn

Secondary:
X
YouTube
Instagram
```

User submits article text.
System:

1. ingests source
2. extracts core ideas
3. creates research packet
4. generates campaign strategy
5. generates content opportunities
6. selects campaign mix
7. creates 7-day calendar
8. generates platform-native content
9. performs QA
10. sends assets to approval
11. user approves
12. scheduler queues posts
13. mock publisher publishes
14. mock analytics return data
15. analytics engine analyzes results
16. optimization agent produces evidence-grounded recommendations

This must be covered by an automated end-to-end test.
56. INITIAL PRIORITY
Prioritize a strong backend and reliable workflow over visual polish.
Order:

```
correctness
security
reliability
architecture
testing
observability
UX
visual polish
```

57. DO NOT
Do not:

* create a monolithic script
* hide everything inside n8n
* hard-code model names
* hard-code credentials
* publish automatically without approval by default
* trust malformed LLM output
* invent analytics
* fabricate API capabilities
* fabricate citations
* silently swallow exceptions
* claim tests passed unless they actually passed
* replace working user code without inspecting it
* build frontend-only mockups with no backend
* stop at pseudocode when implementation is possible

58. FIRST ACTION
Begin by auditing the current development environment and project directory.
Produce:

```
SYSTEM AUDIT
REPOSITORY AUDIT
AVAILABLE SERVICES
LM STUDIO STATUS
DEPENDENCY STATUS
ARCHITECTURE DECISIONS
IMPLEMENTATION PLAN
```

Then immediately begin PHASE 1.
Do not wait for repeated confirmation unless an action is destructive, requires an unavailable credential, incurs external cost, or presents multiple materially different architectural choices.
59. FINAL ACCEPTANCE GATE
Before declaring OmniGrowth complete, perform a formal engineering review.
Verify:
Architecture

* modular
* extensible
* documented

Backend

* API operational
* DB migrations operational
* queue operational

AI

* LM Studio works
* routing works
* structured generation works
* fallback behavior tested

Campaign

* source → strategy → content works

QA

* validation operational
* approval system operational

Publishing

* mock publisher passes
* no duplicate posting

Analytics

* metrics ingest
* derived metrics calculate
* campaign reports work

Optimization

* recommendations use actual stored metrics

Security

* secrets protected
* SSRF mitigations
* inputs validated
* credentials masked

Testing

* unit tests
* integration tests
* workflow tests
* end-to-end test

Documentation

* installation
* configuration
* development
* architecture
* API
* troubleshooting

Finally produce:

```
OMNIGROWTH ENGINEERING REPORT

Architecture:
[summary]

Implemented:
[list]

Test Results:
[number passed / failed]

Security Review:
[results]

Known Limitations:
[list]

External Credentials Still Required:
[list]

Recommended Next Enhancements:
[list]

Production Readiness:
[components ready / components requiring additional work]
```

Never claim "production ready" merely because the happy path works.
Build OmniGrowth as if another senior engineering team will audit the repository immediately after you finish.
