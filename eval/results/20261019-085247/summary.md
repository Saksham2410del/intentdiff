# Evaluation run 20261019-085247

- tickets: 29 (all split), cases: 200 {'match': 42, 'mismatch': 155, 'escalate': 3}
- equivalent mutants (relabelled as match): 9
- variants skipped because they fail outright: 0
- times reuse dry runs cached while labelling, so they understate a cold run

| System | Unsafe passes (wrong script -> MATCH) | False alarms (correct script flagged) | Clean passes | Escalated | Ambiguous tickets escalated | Avg time |
|---|---|---|---|---|---|---|
| b0 | 128/155 (83%) [76-88%] | 0/42 (0%) [0-8%] | 42/42 (100%) | 0/200 (0%) | 0/3 (0%) | 0.00s |
| b1 | 100/155 (65%) [57-72%] | 0/42 (0%) [0-8%] | 42/42 (100%) | 0/200 (0%) | 0/3 (0%) | 0.00s |
| b2 | 54/155 (35%) [28-43%] | 8/42 (19%) [10-33%] | 34/42 (81%) | 0/200 (0%) | 0/3 (0%) | 0.00s |

Unsafe passes by ticket category:

| Category | b0 | b1 | b2 |
|---|---|---|---|
| date_boundary | 12/14 (86%) | 9/14 (64%) | 7/14 (50%) |
| delete | 7/9 (78%) | 3/9 (33%) | 0/9 (0%) |
| exclusion | 13/15 (87%) | 6/15 (40%) | 3/15 (20%) |
| id_list | 21/23 (91%) | 21/23 (91%) | 4/23 (17%) |
| injection | 9/11 (82%) | 9/11 (82%) | 3/11 (27%) |
| insert | 4/4 (100%) | 4/4 (100%) | 0/4 (0%) |
| join | 7/9 (78%) | 2/9 (22%) | 2/9 (22%) |
| multi_statement | 13/17 (76%) | 11/17 (65%) | 9/17 (53%) |
| no_op | 3/4 (75%) | 3/4 (75%) | 0/4 (0%) |
| null_handling | 8/10 (80%) | 6/10 (60%) | 6/10 (60%) |
| predicate | 12/15 (80%) | 9/15 (60%) | 9/15 (60%) |
| relative_value | 8/10 (80%) | 6/10 (60%) | 5/10 (50%) |
| single_id | 11/14 (79%) | 11/14 (79%) | 6/14 (43%) |
