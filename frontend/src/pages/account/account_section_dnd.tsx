import { useRef, useState } from "react";
import { DragDropProvider } from "@dnd-kit/react";
import { useSortable } from "@dnd-kit/react/sortable";
import { move } from "@dnd-kit/helpers";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { GripVertical } from "lucide-react";
import { toast } from "sonner";
import axios from "axios";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AccountIcon } from "@/components/account-icon";
import { Button } from "@/components/ui/button";
import { useWorkspace } from "@/contexts/workspace-context";
import { getAccountLabel } from "@/lib/account-utils";
import { accounts as accountsApi } from "@/lib/api";
import type { Account } from "@/types";

function reconcileIds(localIds: string[], serverIds: string[]) {
  const serverSet = new Set(serverIds);
  const retained = localIds.filter((id) => serverSet.has(id));
  const retainedSet = new Set(retained);
  return [...retained, ...serverIds.filter((id) => !retainedSet.has(id))];
}

function SortableAccount({
  account,
  index,
  canWrite,
  disabled,
}: {
  account: Account;
  index: number;
  canWrite: boolean;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const { ref, handleRef, isDragging } = useSortable({
    id: account.id,
    index,
    disabled,
  });

  return (
    <div
      ref={ref}
      className={`flex items-center gap-3 px-5 py-3 bg-card${isDragging ? " opacity-50" : ""}`}
    >
      {canWrite && (
        <button
          ref={handleRef}
          type="button"
          disabled={disabled}
          className="cursor-grab touch-none rounded p-1 text-muted-foreground hover:text-foreground active:cursor-grabbing disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={`${t("accounts.accountOrder")}: ${getAccountLabel(account)}`}
        >
          <GripVertical size={16} />
        </button>
      )}
      <Link
        to={`/accounts/${account.id}`}
        className="flex min-w-0 flex-1 items-center gap-3"
      >
        <AccountIcon account={account} />
        <span className="truncate text-sm font-medium text-foreground">
          {index + 1}. {getAccountLabel(account)}
        </span>
      </Link>
    </div>
  );
}

export default function AccountSectionDnd({
  accounts,
}: {
  accounts: Account[];
}) {
  const { t } = useTranslation();
  const { canWrite } = useWorkspace();
  const queryClient = useQueryClient();

  const serverIds = accounts.map((account) => account.id);
  const serverIdsKey = serverIds.join(",");
  const [ordering, setOrdering] = useState(() => ({
    sourceIdsKey: serverIdsKey,
    localIds: serverIds,
    dirty: false,
  }));
  if (ordering.sourceIdsKey !== serverIdsKey) {
    setOrdering({
      sourceIdsKey: serverIdsKey,
      localIds: ordering.dirty
        ? reconcileIds(ordering.localIds, serverIds)
        : serverIds,
      dirty: ordering.dirty,
    });
  }
  const accountsById = new Map(accounts.map((account) => [account.id, account]));
  const localItems = ordering.localIds
    .map((accountId) => accountsById.get(accountId))
    .filter((account): account is Account => account !== undefined);
  const beforeDrag = useRef<{
    localIds: string[];
    dirty: boolean;
  } | null>(null);

  const { mutate, isPending } = useMutation({
    mutationFn: accountsApi.bulkOrdering,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      const refreshed =
        queryClient.getQueryData<Account[]>(["accounts"]) ?? localItems;
      const refreshedIds = refreshed.map((account) => account.id);
      setOrdering({
        sourceIdsKey: refreshedIds.join(","),
        localIds: refreshedIds,
        dirty: false,
      });
    },
    onError: (error) => {
      const detail = axios.isAxiosError(error)
        ? error.response?.data?.detail
        : null;
      toast.error(typeof detail === "string" ? detail : t("common.error"));
    },
  });

  return (
    <section className="bg-card rounded-xl border border-border shadow-sm">
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
        <h2 className="text-sm font-medium text-muted-foreground">
          {t("accounts.accountOrder")}
        </h2>
        {canWrite && (
          <Button
            type="button"
            size="sm"
            disabled={!ordering.dirty || isPending}
            onClick={() =>
              mutate(
                localItems.map((account, order) => ({
                  account_id: account.id,
                  order,
                })),
              )
            }
          >
            {t("common.save")}
          </Button>
        )}
      </div>
      <DragDropProvider
        onDragStart={() => {
          beforeDrag.current = {
            localIds: ordering.localIds,
            dirty: ordering.dirty,
          };
        }}
        onDragEnd={(event) => {
          const snapshot = beforeDrag.current;
          beforeDrag.current = null;
          if (event.canceled) {
            setOrdering({
              sourceIdsKey: serverIdsKey,
              localIds: snapshot
                ? reconcileIds(snapshot.localIds, serverIds)
                : serverIds,
              dirty: snapshot?.dirty ?? false,
            });
            return;
          }
          const reordered = move(localItems, event);
          if (reordered === localItems) {
            setOrdering({
              sourceIdsKey: serverIdsKey,
              localIds: snapshot
                ? reconcileIds(snapshot.localIds, serverIds)
                : serverIds,
              dirty: snapshot?.dirty ?? false,
            });
            return;
          }
          setOrdering({
            sourceIdsKey: serverIdsKey,
            localIds: reordered.map((account) => account.id),
            dirty: true,
          });
        }}
      >
        <div className="divide-y divide-muted">
          {localItems.map((account, index) => (
            <SortableAccount
              key={account.id}
              account={account}
              index={index}
              canWrite={canWrite}
              disabled={!canWrite || isPending}
            />
          ))}
        </div>
      </DragDropProvider>
    </section>
  );
}
