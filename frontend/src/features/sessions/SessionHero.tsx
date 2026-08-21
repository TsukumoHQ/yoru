import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { SessionHeroView, type SessionDetail, type CustomRuleInfo } from "@receipt/ui"
import {
  copyReceiptPng,
  downloadReceiptPng,
  downloadReplayGif,
  exportSessionTrail,
  postSummary,
} from "../../lib/api"
import { toast } from "../../components/Toaster"

interface SessionHeroProps {
  session: SessionDetail
  customRuleInfo?: Record<string, CustomRuleInfo>
}

// Share-pivot (TSU-54): the dashboard share action is LOCAL IMAGE EXPORT —
// "Download PNG" / "Copy image" of a self-contained receipt rendered on the
// self-hosted backend. There is no hosted public viewer to link to, so the
// old public-share toggle is intentionally NOT wired here. The backend
// /share endpoints + api.ts share fns stay dormant (private-by-default), not
// ripped — they're just no longer surfaced in the dashboard hero.
export function SessionHero({ session, customRuleInfo }: SessionHeroProps) {
  const [exporting, setExporting] = useState(false)
  const [receiptBusy, setReceiptBusy] = useState(false)
  const [replayBusy, setReplayBusy] = useState(false)
  const queryClient = useQueryClient()

  async function onExport() {
    setExporting(true)
    try {
      await exportSessionTrail(session.id)
    } catch (err) {
      toast.error("Couldn't export trail", err instanceof Error ? err.message : String(err))
    } finally {
      setExporting(false)
    }
  }

  async function onDownloadReceipt() {
    setReceiptBusy(true)
    try {
      await downloadReceiptPng(session.id)
    } catch (err) {
      toast.error("Couldn't render receipt", err instanceof Error ? err.message : String(err))
    } finally {
      setReceiptBusy(false)
    }
  }

  async function onCopyReceipt() {
    setReceiptBusy(true)
    try {
      await copyReceiptPng(session.id)
      toast.success("Receipt image copied", "Paste it anywhere — it's a self-contained PNG.")
    } catch (err) {
      // Clipboard image write can fail (browser support / permissions) — fall
      // back to a download so the share never dead-ends.
      try {
        await downloadReceiptPng(session.id)
        toast.success("Receipt downloaded", "Clipboard image isn't supported here — saved the PNG instead.")
      } catch (err2) {
        toast.error("Couldn't copy receipt", err2 instanceof Error ? err2.message : String(err))
      }
    } finally {
      setReceiptBusy(false)
    }
  }

  async function onDownloadReplay() {
    setReplayBusy(true)
    try {
      await downloadReplayGif(session.id)
    } catch (err) {
      toast.error("Couldn't render replay", err instanceof Error ? err.message : String(err))
    } finally {
      setReplayBusy(false)
    }
  }

  const summaryMutation = useMutation({
    mutationFn: () => postSummary(session.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["session", session.id] })
      queryClient.invalidateQueries({ queryKey: ["session", session.id, "summary"] })
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error("Couldn't generate summary", msg)
    },
  })

  return (
    <SessionHeroView
      session={session}
      customRuleInfo={customRuleInfo}
      onExport={onExport}
      isExporting={exporting}
      onGenerateSummary={() => summaryMutation.mutate()}
      isGeneratingSummary={summaryMutation.isPending}
      onDownloadReceipt={onDownloadReceipt}
      onCopyReceipt={onCopyReceipt}
      isReceiptBusy={receiptBusy}
      onDownloadReplay={onDownloadReplay}
      isReplayBusy={replayBusy}
    />
  )
}
