// Profile chips with remove + undo (PRD F4, 6.4). Controlled: the parent owns which ids are removed.
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
      <ul className="flex flex-col gap-2.5" aria-label="Your profile">
        <AnimatePresence initial={false}>
          {visible.map((item) => (
            <motion.li
              key={item.id}
              layout
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, x: -24, transition: { duration: 0.15 } }}
              transition={{ duration: 0.2, ease: "easeOut" }}
              className="flex items-center gap-3 rounded-2xl border border-line bg-bg-raised py-2 pr-1.5 pl-4"
            >
              <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-crumb shadow-[0_0_8px_rgba(255,210,122,0.7)]" />
              <span className="flex min-w-0 flex-1 flex-col py-1">
                <span className="text-[16px] leading-snug break-words text-ink">{item.text}</span>
                <span className="text-[13px] text-ink-muted">{KIND_LABEL[item.kind]}</span>
              </span>
              <button
                type="button"
                onClick={() => onRemove(item.id)}
                aria-label={`Remove "${item.text}"`}
                className="grid h-11 w-11 shrink-0 cursor-pointer place-items-center rounded-xl text-ink-muted transition-colors hover:bg-bg-sunken hover:text-ink [touch-action:manipulation]"
              >
                <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                  <path d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>

      {visible.length === 0 && (
        <p className="rounded-2xl border border-dashed border-line px-4 py-4 text-[15px] text-ink-muted">
          Nothing left on your list. Breadcrumbs will lean on the conversation itself.
        </p>
      )}

      <div aria-live="polite" className="min-h-11">
        {lastRemoved && (
          <div className="flex items-center justify-between gap-3 rounded-2xl bg-bg-sunken py-1 pr-1 pl-4">
            <p className="min-w-0 truncate text-[15px] text-ink-muted">
              Removed <span className="text-ink">"{lastRemoved.text}"</span>
            </p>
            <button
              type="button"
              onClick={onUndo}
              className="min-h-11 shrink-0 cursor-pointer rounded-xl px-4 text-[15px] font-semibold text-crumb hover:bg-bg-raised [touch-action:manipulation]"
            >
              Undo
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
