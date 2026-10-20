# CampusReserve
**Project Proposal — CSE-416 Software Engineering, Fall 2026**

## 1. Problem

Every registered student organization at Stony Brook University (SBU) uses 25Live, the university's room-reservation system, to request spaces to hold their events. While 25Live supports the university's broader scheduling needs, after conducting interviews with club leaders across technical, media, and performance organizations, we found that it can be difficult to navigate. Leaders commonly describe it as difficult to learn, hard to search, and unreliable about whether a space can actually be booked.

Through our interviews, we identified five recurring areas where the current experience could be improved:

- **Outdated and unintuitive interface.** Several club leaders described the interface as difficult to navigate and unfamiliar to new users. One club leader explained that "it's not intuitive and the interface is old." One former performance-organization leader could not navigate it at all and had to route every booking through their program advisor.
- **Essential features are buried.** Options such as setup and cleanup time are hidden, and the number of filtering options makes room-searching overwhelming rather than helpful. Leaders also cannot see or manage all of their own requests in one place.
- **No communication between organizations.** When multiple organizations use the same space back-to-back, there is no built-in way for them to communicate with one another. This can make it difficult to coordinate transitions, setup, or shared use of a space.
- **Misleading room availability.** A room may appear as available during the search process even though another organization has already submitted a request for it and is awaiting approval, or when the room's building is closed. Club leaders reported that many booking requests were eventually denied because the space was never actually free, forcing them to look for last-minute alternatives.
- **No notifications.** Leaders do not have a reliable, centralized way to keep track of changes to their booking requests or receive timely updates when a request is approved or denied, which compounds every delay mentioned above.

CampusReserve is designed to improve the room-booking experience specifically for student organizations. It presents a clear, modern interface over the current reservation process, built around their needs. It will make relevant room information easier to understand, organize booking requests in one place, display the accurate availability of each room, and introduce features that 25Live lacks, such as clearer notifications and communication between organizations. Confirmed reservations would remain synchronized with the university's existing system of record, helping CampusReserve work alongside the university's current scheduling process rather than replacing it.

## 2. Users and Audience

CampusReserve will serve three types of users who will have different specific needs and permissions. Separating these roles will be important for maintaining security and making sure access is given to the appropriate people.

| Role | Who they are | What they do in CampusReserve |
|------|-------------|--------------------------|
| **Club Executive Members** | Verified E-board members of a registered organization | Search and filter spaces, submit and track booking requests, join waitlists, communicate with other organizations, use the collaboration forum |
| **Administrators** | Student Affairs / USG staff who manage spaces | Review, approve, or deny requests with a stated reason; manage room availability and closures; view analytics on demand and usage |
| **Venue Hosts** | Employees responsible for the buildings and rooms where events are held | Manage the rooms they oversee (details, availability, closures); communicate with organizations booking their spaces |

**Target Audience.** SBU itself is our primary audience. The project is guided by Student Affairs and Undergraduate Student Government (USG) leaders, whose feedback shaped the requirements below.

## 3. Why This Is a Semester of Work

CampusReserve is not a page that a language model produces in a weekend. Its difficulty lives in several genuinely hard, interacting problems:

- **A real-time status engine.** The core value of the product is that a room's status is always accurate — whether it is available, already requested by another group, or genuinely unbookable. Keeping that status correct as requests, approvals, cancellations, and closures happen concurrently is a state-consistency problem.
- **A multi-role approval workflow.** A request moves through states (submitted → pending → approved/denied → possibly cancelled) across two different user roles, each seeing and doing different things. Modeling that workflow and its permissions correctly is substantial.
- **Role-based access control and identity verification.** We must verify that a user is genuinely an E-board member of the organization they claim, and grant each role only the access it needs, only for as long as it needs it.
- **A pre-approved waitlist.** Spaces need a waitlist where the next group can be promoted automatically when an event is cancelled, without a second round of approval.
- **Synchronization with an external system of record.** Confirmed bookings must reconcile with 25Live so we neither double-book nor collide with Registrar academic reservations.

Additionally, meetings will be held consistently with SBU administrators to ensure their needs are met and that their suggestions are taken into consideration for the product.

None of these are scaffolding an agent generates in one pass; the state-consistency, permissions, and external-sync problems require real design decisions and testing across the semester.

## 4. Related Systems

| System | What it is | How CampusReserve differs |
|--------|-----------|----------------------|
| **25Live** | SBU's current reservation system; the system of record, tied to the Registrar and PeopleSoft feeds | We are a friendlier, club-focused interface over the same process; we sync to it rather than replace it, and we filter only to spaces relevant to student organizations |
| **EMS / Skedda / Robin** | Commercial room- and desk-booking products | General-purpose and paid; none model a student-organization approval workflow, E-board verification, or campus collaboration between clubs |

## 5. Requirements

### 5.1 Functional Requirements

**FR-1 — Search & filter.** Users can search and filter spaces by building, capacity, type, and features relevant to student events.
*Why:* leaders told us 25Live's search is "hard to search and look through" and helpful filtering options are hidden — so we prioritize the filters useful to club events rather than the full academic-scheduling set.

**FR-2 — Real-time room status.** Each space displays a real-time status of available, requested (pending another group's approval), or unbookable, with the reason shown for unbookable spaces.
*Why:* leaders reported — "we booked so many events that got denied because we weren't allowed to book those spaces and had no idea since they were listed as available." Rooms still showed as bookable during building closures in 25Live. A status feature ensures a more efficient booking process for clubs.

**FR-3 — Booking requests.** A club leader can submit a booking request for an available time slot, including setup and cleanup time.
*Why:* leaders said essential options "like setup and cleanup time are hidden" in 25Live; surfacing them in the main booking flow helps the club gauge the time and make a smoother transition of the room.

**FR-4 — Request dashboard.** A club leader can view all of their organization's requests and their statuses in one place.
*Why:* it was a direct request — "one thing that I really do hate is that I can't see all of my requests in one place."

**FR-5 — Approval workflow.** An administrator can approve or deny a request and must provide a reason, which is communicated to the requester.
*Why:* leaders are currently left guessing why events are denied. Requiring a stated reason makes the decision transparent and reduces the back-and-forth that delays events.

**FR-6 — Waitlist.** A leader can join a waitlist for a fully-booked popular space; the next waitlisted (pre-approved) request is promoted automatically when a booking is cancelled.
*Why:* popular spaces sit empty when an event is cancelled because there is no way to claim the freed slot. A pre-approved waitlist means availability is not wasted and the next group can proceed without waiting on another approval cycle.

**FR-7 — Notifications.** Users receive notifications on request approval, denial, waitlist promotion, and new messages (in-app for MVP; email as an enhancement).
*Why:* 25Live has no reliable notification system, which leaders said causes "lack of information and communication," creating delays and unnecessary stress. Timely notifications close that gap.

**FR-8 — Messaging.** Users can message other organizations, venues, and administrators to coordinate shared use of spaces.
*Why:* there is no way to communicate through 25Live, so groups sharing a space "could have resolved issues with planning ahead of time rather than figuring it out during our event time." Direct messaging lets them coordinate.

**FR-9 — Collaboration forum.** Users can post to a forum to propose collaborations and offer or request shared resources and storage.
*Why:* organizations often want to collaborate or share resources and storage, but have no common space to make those requests. The forum creates that space.

**FR-10 — Room detail.** Each space has a detailed view with its features and a visual preview of the room.
*Why:* leaders frequently don't have a full grasp of what a space offers or what events it can support. A detailed view with a visual preview helps them understand what they're working with before they commit.

**FR-11 — 25Live sync.** Confirmed bookings are reconciled with the 25Live system of record through a sync layer.
*Why:* 25Live remains the university's system of record and ties into the Registrar's academic scheduling. Syncing to it — rather than replacing it — means our bookings don't collide with academic reservations and administrators keep one authoritative source of truth.

**FR-12 — Analytics.** Administrators can view analytics on room demand, peak booking times, and usage patterns.
*Why:* requested directly by Student Affairs (Ahmed Belazi, Executive Director for Strategic Analytics and Technologies). Administrators need to see how spaces are used to allocate them well and to confirm that the technology spend is justified. Since SBU is our client, this helps our stakeholders make decisions and feel confident in the platform.

### 5.2 Non-Functional Requirements

**Security and privacy.** These requirements stem directly from Student Affairs feedback, which prioritizes confidentiality.

- **NFR-1 — Role-based access.** Each role can access only the data and actions its role permits; E-board membership is verified before a leader gains booking rights.
- **NFR-2 — Least privilege.** Access is scoped to what a user needs and for only as long as they need it (e.g., leadership rights tied to a current term).
- **NFR-3 — Credential protection.** Passwords, sessions, and user data are hashed and protected so that confidential information is not exposed across organizations.

**Accessibility.** Student Affairs would like our application to consider identity, linguistic, disability, learning, and neurodivergent needs.

- **NFR-4 — Contrast controls.** Users can choose among color/contrast sets rather than a single fixed theme for visual impairments.
- **NFR-5 — Simplification layer.** A simplified view reduces on-screen complexity for users who need it.
- **NFR-6 — Standards.** The interface incorporates WCAG AA: keyboard navigability, visible focus, screen-reader labels, and sufficient contrast by default.

**Performance and reliability.**

- **NFR-7 — Status latency.** A change in a room's status is reflected to other users within a few seconds.
- **NFR-8 — No false availability.** The system never shows a space as freely bookable when it has a pending request or is closed.

## 6. User Feedback on 25Live

The inspiration for this application comes from a significant common frustration expressed by club leaders across a variety of organizations. These frustrations include:

**A steep learning curve and outdated UX make the interface difficult to navigate and learn**
> "It's not intuitive and the interface is old" — Tech Club E-Board Member
>
> "I have tried to help my organization with booking but was unable to navigate 25Live and had to coordinate with our program advisor instead." — Former Performance Organization E-Board Member

**Buried essential features and overwhelming filtering options make prioritized information harder to find**
> "A lot of features, like setup and cleanup time, are hidden" — Tech Club E-Board Member
>
> "It's hard to search and look through things" — Media Club E-Board Member
>
> "One thing that I really do hate is that I can't see all of my requests in one place." — Performance Organization E-Board Member

**Lack of communication features to facilitate events between organizations**
> "We have had issues with other groups that could have been resolved with planning ahead of time rather than figuring it out during our event time." — Former Performance Organization E-Board Member

**Confusing availability statuses and conflicting booking periods**
> "We booked so many events that got denied because we weren't allowed to book those spaces and had no idea since they were listed as available." — Performance Organization E-Board Member

## 7. Architecture Sketch

CampusReserve is a three-tier web application. A React frontend serves both roles; a Python and FastAPI backend holds the authentication, scope-based access, booking, approval-workflow, status, and waitlist logic; and a PostgreSQL database, accessed through the SQLAlchemy ORM, stores users, organizations, venues, rooms, requests, messages, and forum content.

PostgreSQL is chosen deliberately: CampusReserve's core guarantee is that a room is never double-booked, which is a transactional problem. Postgres's ACID transactions let the backend check for a conflicting booking and commit an approval as a single atomic operation, so two administrators approving overlapping requests concurrently cannot both succeed. Our data is also highly relational, which fits a relational schema naturally.

The 25Live integration is intentionally designed as a sync layer with a swappable adapter. Rather than depending on write access to a production university system, we will build against a mock/test 25Live and treat real API access as an enhancement. This lets us demonstrate the full application end-to-end regardless of integration access, and it is why the diagram shows the real system as a dashed, replaceable target. This will allow us to properly test the functionality of our product prior to deployment.

A rendered architecture diagram is shown in the `artifacts` folder of this GitHub repository (`architecture-sketch.png`).

Some potential UI mockups are shown in the `artifacts` and `mock-UI` folders of this GitHub repository (`mock-UI-view1.png` and `mock-UI-view2.png`).

## 8. Scope and Phased Plan

We scope deliberately. The Minimum Viable Product is the club-leader booking flow that displays accurate room availability, the piece that solves the most painful problem for current 25Live users. Coordination, analytics, and integration features are layered on in later phases, and the most open-ended features are named explicitly as enhancements so the scope stays honest.

| Phase | Target | Scope |
|-------|--------|-------|
| **Phase 1 (MVP)** | By M3 | Accounts and role-based auth; search and filter; accurate room availability; submit and track booking requests; admin approval with reason; club-leader request dashboard. |
| **Phase 2** | By M4 | Waitlist with automatic promotion; in-app notifications; messaging between organizations, venues, and admins. |
| **Phase 3** | By M5 | Collaboration forum; analytics layer; accessibility controls (contrast sets, simplification layer); 25Live sync against the test environment. |
| **Enhancements** | Stretch / future | Email notifications; 360° room view (uploaded panoramas rather than modeled 3D); production 25Live integration. Documented for a future team to continue. |

## 9. Team Roles

| Team Member | Workstream | Responsibility |
|-------------|-----------|----------------|
| **Kelly Fung** — PR Officer of a club | Frontend & accessibility | React interface across both roles; accessibility controls; demo readiness. |
| **Sarah Julian** — Treasurer of a club for 2 years | Backend & workflow | Auth, role-based access, booking and approval-workflow logic, status engine, waitlist. |
| **Susan Qu** | Data & integration | MongoDB schema; the 25Live sync adapter and its mock/test target. |
| **Sarah Zhang** | Notifications, forum & analytics | Notification service, messaging, collaboration forum, and the analytics layer. |
