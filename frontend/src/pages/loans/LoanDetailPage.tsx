import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Download, RefreshCw, DollarSign } from 'lucide-react';
import { formatCurrency } from '@/lib/format';
import { LoanScheduleTable } from './LoanScheduleTable';
import { PrepaymentDialog } from './PrepaymentDialog';
import { LoanAnalyticsCharts } from './LoanAnalyticsCharts';
import { LoanSimulations } from '@/components/loans/LoanSimulations';
import { CombinedLoanSimulator } from '@/components/loans/CombinedLoanSimulator';
import { useState } from 'react';

interface LoanOverview {
  progress_percent: number;
  emis_paid: number;
  emis_remaining: number;
  principal_paid: string;
  principal_remaining: string;
  interest_paid: string;
  interest_remaining: string;
  total_prepayments: string;
}

async function fetchLoanOverview(accountId: string): Promise<LoanOverview> {
  const response = await fetch(`/api/v1/loans/${accountId}/overview`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('token')}`, 'X-Workspace-Id': localStorage.getItem('workspace_id') || '' },
  });
  if (!response.ok) throw new Error('Failed to fetch loan overview');
  return response.json();
}

async function exportScheduleCSV(accountId: string) {
  const response = await fetch(`/api/v1/loans/${accountId}/schedule/export`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('token')}`, 'X-Workspace-Id': localStorage.getItem('workspace_id') || '' },
  });
  if (!response.ok) throw new Error('Failed to export schedule');

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `loan_${accountId}_schedule.csv`;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}

export function LoanDetailPage() {
  const { accountId } = useParams<{ accountId: string }>();
  const [prepaymentDialogOpen, setPrepaymentDialogOpen] = useState(false);

  const { data: overview, isLoading, error, refetch } = useQuery({
    queryKey: ['loan-overview', accountId],
    queryFn: () => fetchLoanOverview(accountId!),
    enabled: !!accountId,
  });

  if (isLoading) {
    return (
      <div className="container mx-auto py-8">
        <div className="flex justify-center">Loading loan details...</div>
      </div>
    );
  }

  if (error || !overview) {
    return (
      <div className="container mx-auto py-8">
        <Alert variant="destructive">
          <AlertDescription>Failed to load loan details</AlertDescription>
        </Alert>
      </div>
    );
  }

  const handleExport = async () => {
    try {
      await exportScheduleCSV(accountId!);
    } catch (err) {
      console.error('Export failed:', err);
    }
  };

  return (
    <div className="container mx-auto py-8 space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Loan Details</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
          <Button variant="outline" onClick={handleExport}>
            <Download className="h-4 w-4 mr-2" />
            Export CSV
          </Button>
          <Button onClick={() => setPrepaymentDialogOpen(true)}>
            <DollarSign className="h-4 w-4 mr-2" />
            Make Prepayment
          </Button>
        </div>
      </div>

      {/* Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Progress</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{overview.progress_percent.toFixed(1)}%</div>
            <p className="text-xs text-muted-foreground mt-1">
              {overview.emis_paid} of {overview.emis_paid + overview.emis_remaining} EMIs paid
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Principal Paid</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(parseFloat(overview.principal_paid))}</div>
            <p className="text-xs text-muted-foreground mt-1">
              Remaining: {formatCurrency(parseFloat(overview.principal_remaining))}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Interest Paid</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(parseFloat(overview.interest_paid))}</div>
            <p className="text-xs text-muted-foreground mt-1">
              Remaining: {formatCurrency(parseFloat(overview.interest_remaining))}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Prepayments</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(parseFloat(overview.total_prepayments))}</div>
            <p className="text-xs text-muted-foreground mt-1">Total prepaid</p>
          </CardContent>
        </Card>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="schedule" className="w-full">
        <TabsList>
          <TabsTrigger value="schedule">Schedule</TabsTrigger>
          <TabsTrigger value="analytics">Analytics</TabsTrigger>
          <TabsTrigger value="simulations">Simulations</TabsTrigger>
          <TabsTrigger value="combined">Combined plan</TabsTrigger>
        </TabsList>

        <TabsContent value="schedule" className="mt-4">
          <LoanScheduleTable accountId={accountId!} />
        </TabsContent>

        <TabsContent value="analytics" className="mt-4">
          <LoanAnalyticsCharts accountId={accountId!} />
        </TabsContent>

        <TabsContent value="simulations" className="mt-4">
          <LoanSimulations
            accountId={accountId!}
            currentEmi={parseFloat(overview.principal_paid) + parseFloat(overview.interest_paid)}
            outstandingBalance={parseFloat(overview.principal_remaining)}
            currentRate={0}
            remainingMonths={overview.emis_remaining}
          />
        </TabsContent>

        <TabsContent value="combined" className="mt-4">
          <CombinedLoanSimulator accountId={accountId!} />
        </TabsContent>
      </Tabs>

      {/* Prepayment Dialog */}
      <PrepaymentDialog
        accountId={accountId!}
        open={prepaymentDialogOpen}
        onOpenChange={setPrepaymentDialogOpen}
        onSuccess={() => refetch()}
      />
    </div>
  );
}

export default LoanDetailPage
