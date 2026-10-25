// Profile chips with remove + undo (PRD F4, 6.4). Controlled: the parent owns which ids are removed.
// Each chip is a white pill with its kind and an x; long items wrap inside the pill.
import { AnimatePresence, motion } from "framer-motion";
import type { ProfileItem } from "../lib/types";
import { KIND_LABEL } from "./phone/flow";

interface ProfileChipsProps {
  items: ProfileItem[];
  removedIds: string[]; // in removal order; the last one is what Undo restores
  onRemove: (id: string) => void;
  onUndo: () => void;
}

export function ProfileChips({ items, removedIds, onRemove, onUndo }: ProfileChipsProps) {
  const removed = new Set(removedIds);
  const visible = items.filter((i) => !removed.has(i.id));
  const lastRemoved = removedIds.length ? items.find((i) => i.id === removedIds[removedIds.length - 1]) : undefined;

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-wrap gap-2" aria-label="Your profile">
        <AnimatePresence initial={false}>
          {visible.map((item) => (
            <motion.li
              key={item.id}
              layout
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.92, transition: { duration: 0.15 } }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="flex max-w-full items-center gap-1 rounded-[26px] bg-bg-raised py-1 pr-1 pl-4 shadow-pill"
            >
              <span className="flex min-w-0 flex-col py-1.5">
                <span className="text-[12px] leading-tight font-medium text-ink-muted">{KIND_LABEL[item.kind]}</span>
                <span className="text-[16px] leading-snug break-words text-ink">{item.text}</span>
              </span>
              <button
                type="button"
                onClick={() => onRemove(item.id)}
                aria-label={`Remove "${item.text}"`}
                className="grid h-11 w-11 shrink-0 cursor-pointer place-items-center rounded-full text-ink-muted transition-colors hover:bg-ink/5 hover:text-ink [touch-action:manipulation]"
              >
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>

      {visible.length === 0 && (
        <p className="rounded-3xl border border-dashed border-line-strong px-4 py-4 text-[15px] text-ink-muted">
          Nothing left on your list. Breadcrumbs will lean on the conversation itself.
        </p>
      )}

      <div aria-live="polite" className="min-h-11">
        {lastRemoved && (
          <div className="flex items-center justify-between gap-3 rounded-full bg-bg-sunken/70 py-1 pr-1 pl-4">
            <p className="min-w-0 truncate text-[15px] text-ink-muted">
              Removed <span className="text-ink">"{lastRemoved.text}"</span>
            </p>
            <button
              type="button"
              onClick={onUndo}
              className="min-h-11 shrink-0 cursor-pointer rounded-full bg-bg-raised px-4 text-[15px] font-medium text-ink shadow-pill hover:bg-bg [touch-action:manipulation]"
            >
              Undo
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
