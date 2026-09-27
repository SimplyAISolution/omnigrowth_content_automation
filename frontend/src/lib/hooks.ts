import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { newIdempotencyKey } from "../api/client";

/**
 * A user action that changes server state. One Idempotency-Key is kept for the action until
 * it succeeds or the user changes the input, so pressing "Retry" after a network failure is
 * recognised by the API as the same request (no duplicate job or post).
 */
export function useAction<TInput, TResult>(
  run: (input: TInput, idempotencyKey: string) => Promise<TResult>,
  options: { invalidate?: QueryKey[]; onSuccess?: (result: TResult, input: TInput) => void } = {},
) {
  const queryClient = useQueryClient();
  const keyRef = useRef<{ key: string; input: string } | null>(null);
  const [lastInput, setLastInput] = useState<TInput | null>(null);
  const mutation = useMutation({
    mutationFn: async (input: TInput) => {
      const fingerprint = JSON.stringify(input);
      if (!keyRef.current || keyRef.current.input !== fingerprint) {
        keyRef.current = { key: newIdempotencyKey(), input: fingerprint };
      }
      return run(input, keyRef.current.key);
    },
    onSuccess: async (result, input) => {
      keyRef.current = null;
      await Promise.all((options.invalidate ?? []).map((queryKey) => queryClient.invalidateQueries({ queryKey })));
      options.onSuccess?.(result, input);
    },
  });
  const submit = useCallback(
    (input: TInput) => {
      setLastInput(input);
      mutation.mutate(input);
    },
    [mutation],
  );
  const retry = useCallback(() => {
    if (lastInput !== null) mutation.mutate(lastInput);
  }, [lastInput, mutation]);
  return { submit, retry, ...mutation };
}
