export const SAFETY_LOCKED_HERMES_JOB_IDS = new Set([
  'b03a2d0f1149',
]);

export function isSafetyLockedHermesJob(
  hermesJobId?: string | null,
) {
  return Boolean(
    hermesJobId &&
      SAFETY_LOCKED_HERMES_JOB_IDS.has(hermesJobId),
  );
}
