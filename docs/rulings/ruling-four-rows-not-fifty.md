# Four rows, not fifty

**Ruled 24 September 2026, by the user, with two drawings.** Newest ruling wins
(CLAUDE.md rule 4). This supersedes the flat agent list whenever a right-hands
roster exists (`docs/right-hands.md`); with no roster the panel is as before.

> "Instead of seeing like 50 different things, hierarchical."
> "So that it never really overwhelms me."
> "When I opened that agent up, I really don't have context."

## The rules

1. **The grid is the right-hands, in the roster's order.** Director, Yobi1,
   Sys-3PO, TeamChat Manager (a placeholder until it exists: greyed, "isn't
   running yet"). Every other agent is filed: one page away, never on the grid.
2. **A dot means "needs me", and nothing else is on the row.** Filled when the
   hand has something for the user (a waiting turn, a process stopped on him,
   or, for Director, a needs-you count above zero); hollow otherwise. No id,
   no reason column. This amends the three-lamp ruling for pinned rows only:
   blue is not drawn on a right-hand's row.
3. **Opening Director is an accordion.** One short line ("5 things need you"),
   the first three items, then "more…". The tap also says one sentence: the
   count and the first thing, subject named.
4. **Every line is a door to context, never to a pane.** An item opens a card
   named Director with that item said in plain words, and Director becomes the
   reply target: what is said or typed next goes to `director ask`. "more…" and
   the summary line ask Director for the whole list. ⌃⌃ on Director's card is
   More.
5. **Only right-hands speak.** Workers' turns do not chime or announce. The
   one-sentence-an-hour limit on Director's own hails is Director's to keep.
6. **Go to Agent opens Ghostty**, never Terminal, attached to the pane where
   it already lives: `open -na Ghostty --args -e tmux -L <socket> attach -t
   <session>`. It never moves a session into this app's socket.
7. **The card names the speaker by the user's name for it**: "Director".
8. **A spoken Director line is Director's own words** (its card or its hail),
   never a model's paraphrase of its turn.

## Tap-to-explain (25 Sep)

An item tap asks Director `tell me more about <agent>` in the card's thread. Director's
`explain_item` intent (shipped 25 Sep) reads the item and never types into a pane, and
the item is then in focus, so a "yes" said next answers it. The request names the agent
rather than a number, because the accordion's items come from `director --json status`
and Director numbers the list it last showed that thread. If Director does not answer,
the card reads the item's own line.
