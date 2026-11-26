# The admission check: adding or changing a filter

You are checking one candidate filter before it enters the standard order. You have: the shared rules; every filter in the order, with its position; the candidate, and the position proposed for it (or its current position, if it is a change); and the style rules. Lint (`book lint`) owns what code can check and fix; a filter never repeats it.

1. **Ownership.** List each concern the candidate acts on. For each, name any filter in the order that already acts on it. Two owners for one concern is a conflict, even if they agree today.
2. **Earlier filters.** For every filter before the proposed position: could the candidate undo, rework or contradict a change that filter makes (cut what it adds, add what it cuts, strengthen what it weakens, reword what it protects)? Quote both lines.
3. **Later filters.** For every filter after the proposed position: could that filter undo, rework or contradict a change the candidate makes? Quote both lines.
4. **Style rules, shared rules and lint.** Quote any line of the candidate that a style rule or a shared rule forbids, or that restates one (a restatement is to be deleted, not kept), and any line that does work lint owns.
5. **Position.** Say whether another position removes a conflict found in 2 or 3, and which.
6. **Size.** Count the candidate's characters; flag anything over 2,000 and any example longer than one line.

Report a numbered list of conflicts, each with the two quoted lines, the kind (two owners, undo, contradiction, restates a rule, forbidden by a rule, lint's work) and the smallest change to the candidate that removes it. If there are none, say "No conflicts" and give the character count. Change nothing yourself. The candidate enters the order only when the author has settled every conflict.
