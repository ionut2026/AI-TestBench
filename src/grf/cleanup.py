"""Aggregated owned-resource cleanup with an explicit verified-final-state/quarantine contract."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CleanupStep:
    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class CleanupResult:
    steps: tuple[CleanupStep, ...]
    final_state_verified: bool
    quarantined: bool

    def as_dict(self) -> dict:
        return asdict(self)


class CleanupError(AssertionError):
    def __init__(self, result: CleanupResult):
        self.result = result
        failed = "; ".join(f"{step.name}: {step.detail}" for step in result.steps if step.status != "PASS")
        super().__init__(f"cleanup failed; environment quarantined: {failed}")


class CleanupGuard:
    """An in-process guard; the protected controller must persist quarantine across process restarts."""

    def __init__(self) -> None:
        self.quarantined = False
        self.last_result: CleanupResult | None = None

    def require_ready(self) -> None:
        if self.quarantined:
            raise AssertionError("environment is quarantined; authorized reconciliation is required before another test")

    def reconcile(self, verify: Callable[[], bool]) -> None:
        """Release only after a product-owned probe explicitly verifies the approved baseline."""
        if verify() is not True:
            raise AssertionError("reconciliation did not verify the approved final state; quarantine remains")
        self.quarantined = False

    def run(self, actions: Sequence[tuple[str, Callable[[], None]]], verify: Callable[[], bool], *,
            publish: Callable[[CleanupResult], None] | None = None) -> CleanupResult:
        """Attempt every named action and final probe, then fail explicitly on any unresolved obligation."""
        names = [name for name, _ in actions]
        if any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != len(names) \
                or set(names).intersection({"final-state", "publish", "existing-quarantine"}):
            raise ValueError("cleanup actions need unique nonempty names")
        if any(not callable(action) for _, action in actions) or not callable(verify):
            raise ValueError("cleanup actions and final-state probe must be callable")
        steps = [CleanupStep("existing-quarantine", "FAIL", "authorized reconciliation still required")] \
            if self.quarantined else []
        interrupted: BaseException | None = None
        for name, action in actions:
            try:
                if action() is not None:
                    raise ValueError("cleanup action must return None on success, or raise on failure")
                steps.append(CleanupStep(name, "PASS"))
            except BaseException as err:
                steps.append(CleanupStep(name, "FAIL", f"{type(err).__name__}: {err}"))
                if not isinstance(err, Exception) and interrupted is None:
                    interrupted = err
        final_state = False
        try:
            if verify() is not True:
                raise AssertionError("approved final state was not explicitly verified")
            final_state = True
            steps.append(CleanupStep("final-state", "PASS"))
        except BaseException as err:
            steps.append(CleanupStep("final-state", "FAIL", f"{type(err).__name__}: {err}"))
            if not isinstance(err, Exception) and interrupted is None:
                interrupted = err
        # A successful later cleanup does not silently clear an earlier unresolved quarantine.
        self.quarantined = self.quarantined or any(step.status != "PASS" for step in steps)
        result = CleanupResult(tuple(steps), final_state, self.quarantined)
        self.last_result = result
        if publish is not None:
            try:
                publish(result)
            except BaseException as err:
                self.quarantined = True
                steps.append(CleanupStep("publish", "FAIL", f"{type(err).__name__}: {err}"))
                result = CleanupResult(tuple(steps), final_state, True)
                self.last_result = result
                if not isinstance(err, Exception) and interrupted is None:
                    interrupted = err
        if interrupted is not None:
            interrupted.add_note(f"Cleanup outcome: {result.as_dict()}")
            raise interrupted
        if result.quarantined:
            raise CleanupError(result)
        return result
