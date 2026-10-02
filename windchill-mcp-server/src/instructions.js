/** Agent playbook: sent to MCP clients as server instructions and installed as a Copilot instructions file. */
export const INSTRUCTIONS = `# Windchill RV&S (PTC Integrity / MKS) — how to find data

You have read-only access to Windchill RV&S through the windchill MCP tools (rvs_*).
Work autonomously: when asked about RV&S data, investigate with the tools instead of asking the user to clarify.
Only ask if the tools genuinely cannot disambiguate after the steps below.

## Playbook
1. Unknown or ambiguous term (module, product, component, project, person, document, query)?
   Call rvs_find(query) first. It shows matching projects, item types, states, fields, saved queries and users,
   plus where items mentioning the term live (counts by type / project / state). Item IDs are looked up directly.
   Text search is case-insensitive and tries spelling variants automatically ("SDCardCorrupted" also finds
   "SdCardCorrupted" and "SD card corrupted"), so one rvs_find call is usually enough. Don't retry with other casings.
2. Then search with rvs_search_items (lists) or rvs_count_items (totals / breakdowns). Names are resolved loosely:
   types "user story" -> "ASD-User Story", states "Draft" -> the type's own state ("ASD-Draft"),
   projects "Replicant" -> every project whose name starts with that word (+ sub-projects), users by name or e-mail.
   Check the "resolved" list in the response to confirm the interpretation.
3. Modules/components are usually tagged in the Summary, e.g. "(PRA) ..." or "[PRA] ...":
   use text "PRA" with textFields ["Summary"] (match both bracket styles; don't search for "(PRA)" only).
4. Any other field: use where, e.g. {"Product":"Replicant B"}, {"team":"Replicant Team"},
   {"Priority":["High","Critical"]}, {"Relevant Explorative Test":{"op":"empty"}},
   {"Created Date":{"op":">=","value":"2026-01-01"}}. Learn a type's fields with rvs_describe_type and a
   field's allowed values with rvs_describe_field.
5. Empty result? Read "hints" and widen step by step: drop the state filter, use textMatch "allWords"/"anyWord",
   add textFields (Text, Description), broaden projects, or go back to rvs_find. Don't conclude "nothing exists"
   after a single empty query.
6. Teams often encode conventions in saved queries: check rvs_find's savedQueries / rvs_list_queries and run them
   with rvs_run_query (names are matched loosely); its queryDefinition shows how the team filters.
7. Details: rvs_get_items (fields, history, attachments). Traceability: rvs_get_relationships
   (requirement -> specification -> test case -> test steps). Documents: rvs_get_document (Markdown, paged).
   Test results and anything else read-only: rvs_run_command (e.g. app "tm", command "results").
8. Use rvs_count_items for "how many" questions instead of paging; page lists with offset when hasMore is true.
9. When answering, state the scope that was searched (resolved projects, types, states, filters) and present
   items as a table (ID, Summary, State, Project, ...). If a term had several interpretations, cover all of them.
`;
