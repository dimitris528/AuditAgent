"use client";

import { useCallback, useMemo, useState } from "react";

/**
 * Checkbox selection over a list of ids.
 *
 * Held as a Set of STRING ids regardless of what the payload used — the
 * dashboard serialises transaction ids as strings and client ids as numbers,
 * and one key type for both is what keeps every caller from remembering which
 * is which. The bulk endpoints coerce either way (server/main.BulkIds).
 *
 * `visible` is the list currently on screen, which is what "select all" and the
 * header checkbox state are computed against — NOT the full dataset. Selecting
 * all while a search filter is active must mean "these seven", or the next
 * click deletes rows the user cannot see.
 *
 * Selections are pruned to what is still visible when read back, so a row that
 * disappears behind a filter change cannot ride along invisibly into a delete.
 */
export function useSelection(visible: readonly string[]) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const visibleSet = useMemo(() => new Set(visible), [visible]);

  // The effective selection: what was ticked AND is still on screen. Derived
  // rather than synced with an effect — an effect would fire a render late,
  // and for one frame the action bar would offer to delete a hidden row.
  const ids = useMemo(
    () => visible.filter((id) => selected.has(id)),
    [visible, selected],
  );

  const toggle = useCallback((id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);

  const clear = useCallback(() => setSelected(new Set()), []);

  const toggleAll = useCallback(() => {
    setSelected((current) => {
      const everyVisible = visible.length > 0 && visible.every((id) => current.has(id));
      if (everyVisible) {
        // Deselect only what is on screen, so a filtered-out selection made
        // before the search box was touched is not silently dropped.
        const next = new Set(current);
        for (const id of visible) next.delete(id);
        return next;
      }
      return new Set([...current, ...visible]);
    });
  }, [visible]);

  const isSelected = useCallback((id: string) => selected.has(id), [selected]);

  const allVisible = visible.length > 0 && ids.length === visible.length;
  return {
    /** Selected ids that are still visible, in the order they appear. */
    ids,
    count: ids.length,
    isSelected,
    toggle,
    toggleAll,
    clear,
    /** Header checkbox state — `some` drives the indeterminate dash. */
    allVisible,
    someVisible: ids.length > 0 && !allVisible,
    /** True when at least one id was ticked but is now filtered out of view. */
    hidden: selected.size > ids.length,
    visibleCount: visibleSet.size,
  };
}
