import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { AlertTriangle, TrendingUp, Calendar } from 'lucide-react';
import { formatCurrency } from '@/lib/format';
import { Link } from 'react-router-dom';

interface LoanDashboardData {
  next_due_payments: Array<{
    account_id: string;
    account_name: string;
    due_date: string;
    emi_amount: string;
    days_until_due: number;
  }>;
  recent_payments: Array<{
    account_name: string;
    payment_date: string;
    amount_paid: string;
  }>;
  alerts: Array<{
    type: string;
    message: string;
    account_id: string;
  }>;
  total_monthly_emi: string;
  total_outstanding: string;
}

async function fetchLoanDashboard(): Promise<LoanDashboardData> {
  const response = await fetch('/api/v1/loans/dashboard', {
    headers: { Authorization: `Bearer ${localStorage.getItem('token')}`, 'X-Workspace-Id': localStorage.getItem('workspace_id') || '' },
  });
  if (!response.ok) throw new Error('Failed to fetch loan dashboard');
  return response.json();
}

export function LoanDashboardWidget() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['loan-dashboard'],
    queryFn: fetchLoanDashboard,
    refetchInterval: 300000, // 5 minutes
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Loan Overview</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex justify-center py-8">Loading...</div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Loan Overview</CardTitle>
        </CardHeader>
        <CardContent>
          <Alert variant="destructive">
            <AlertDescription>Failed to load loan data</AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  if (!data) return null;

  const hasAlerts = data.alerts.length > 0;
  const upcomingPayments = data.next_due_payments.filter(p => p.days_until_due <= 7);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Loan Overview</span>
          <Link to="/loans">
            <Button variant="ghost" size="sm">View All</Button>
          </Link>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Summary Stats */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-sm text-muted-foreground">Total Outstanding</p>
            <p className="text-2xl font-bold">{formatCurrency(parseFloat(data.total_outstanding))}</p>
          </div>
          <div>
            <p className="text-sm text-muted-foreground">Monthly EMI</p>
            <p className="text-2xl font-bold">{formatCurrency(parseFloat(data.total_monthly_emi))}</p>
          </div>
        </div>

        {/* Alerts */}
        {hasAlerts && (
          <Alert variant="warning">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              <div className="space-y-1">
                {data.alerts.slice(0, 3).map((alert, idx) => (
                  <div key={idx} className="text-sm">{alert.message}</div>
                ))}
              </div>
            </AlertDescription>
          </Alert>
        )}

        {/* Upcoming Payments */}
        {upcomingPayments.length > 0 && (
          <div>
            <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
              <Calendar className="h-4 w-4" />
              Due This Week
            </h4>
            <div className="space-y-2">
              {upcomingPayments.map((payment) => (
                <div
                  key={payment.account_id}
                  className="flex justify-between items-center p-2 rounded-md bg-muted/50"
                >
                  <div>
                    <p className="text-sm font-medium">{payment.account_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(payment.due_date).toLocaleDateString()}
                    </p>
                  </div>
                  <p className="text-sm font-medium">{formatCurrency(parseFloat(payment.emi_amount))}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Recent Payments */}
        {data.recent_payments.length > 0 && (
          <div>
            <h4 className="text-sm font-medium mb-2 flex items-center gap-2">
              <TrendingUp className="h-4 w-4" />
              Recent Payments
            </h4>
            <div className="space-y-2">
              {data.recent_payments.slice(0, 3).map((payment, idx) => (
                <div key={idx} className="flex justify-between items-center text-sm">
                  <span className="text-muted-foreground">{payment.account_name}</span>
                  <span className="font-medium">{formatCurrency(parseFloat(payment.amount_paid))}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
