1. One test per observable behaviour of the specification; negative cases of the same specification are separate tests.
2. Name: `SDS-<id> <behaviour in title case>`. Put the specification text verbatim into `[Documentation]`
   and explain any interpretation you made on extra `...` lines.
3. Tags: exactly the tags under "Tags" below, plus `review:pending`, plus capability tags:
   `needs:twin` when the test drives the simulated hardware (Trigger Hardware Action other than the emergency stop,
   racks, Wait For Hardware Command as a *required* step, or COP faults with `Set Hardware Faults`);
   `needs:hardware-action` (with `Require Hardware Action`) when its only hardware step is `Trigger Emergency Stop`,
   which the twin does offline and an operator does on the rig; `needs:operator` when it uses `Operator Action`;
   `requires:restart` when it restarts appSMM; `requires:broker-restart` when it restarts the MQTT broker;
   `needs:applog` when it reads appSMM's log files (`ICD Messages Should Be Logged By appSMM`).
   A test that
   covers several specifications carries `SDS-<id>` and `spechash:<id>:<hash>` for each of them
   (a plain `spechash:<hash>` only works on a single-specification test).
4. Only use keywords from the list below and Robot BuiltIn/Collections. No `Sleep` (`smm-auto lint` rejects it):
   wait for messages (`Wait For Message`, `Wait For Message Sequence`, `Wait For System State`), prove absence
   with `Message Should Not Arrive`, simulate a lost Bridge with `Interrupt Bridge Connection`. Each wait consumes
   the message it matched (two waits need two messages); consecutive waits do not imply order, so assert order
   with `Wait For Message Sequence` (or `since=last`).
5. Preconditions with `Bring SMM To State`, `Bring SMM To E-Stop With Shutdown` or
   `Restart appSMM And Wait Until NotInitialized`; they are not verification steps.
6. Assert what the specification says and nothing more: states, message order, fields, topic (`@topic=/is/iw/tx`),
   ICD schema validity (`Received Messages Should Be Schema Valid`). Values the specification leaves open
   (e.g. "EventId": ??) are logged, not asserted.
7. Timeouts come from variables (`${RESPONSE_TIMEOUT}`, `${STARTUP_TIMEOUT}`, `${INIT_TIMEOUT}`,
   `${RECOVER_TIMEOUT}`, `${QUIET_PERIOD}`, `${SHORT_QUIET_PERIOD}`). A time stated by the specification goes into a
   suite variable `${SDS_<id>_LIMIT}` (quote it in the documentation); literal `timeout=`/`duration=` values are
   rejected by `smm-auto lint`. Assert the stated time itself with `Time Between Should Be Less Than` (and, for
   "if no reply within N s", `Time Between Should Be At Least`); a missing or late hardware reply is simulated
   with `Set Hardware Faults` (`DeInitializeRsp | action=drop`, `... | delay=15s`).
8. If the behaviour cannot be observed through the ICD, the hardware twin or appSMM's log, do not write a test: say why,
   so the specification can be listed under `[deferred]` in the scope file.
9. Add the test to the suite file named under "Target"; keep the suite's Settings unchanged.
10. A human reviews every generated test against the specification and removes `review:pending`.
