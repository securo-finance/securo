import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Calculator, TrendingDown, DollarSign, Percent } from 'lucide-react';
import { formatCurrency } from '@/lib/format';

interface LoanSimulationsProps {
  accountId: string;
  currentEmi: number;
  outstandingBalance: number;
  currentRate: number;
  remainingMonths: number;
}

interface EarlyPaymentResult {
  current_state: {
    outstanding_balance: number;
    current_emi: number;
    remaining_months: number;
    current_interest_rate: number;
  };
  prepayment: {
    amount: number;
    date: string;
    new_principal: number;
  };
  reduce_emi: {
    new_emi: number;
    emi_reduction: number;
    emi_reduction_percent: number;
    tenure_months: number;
    interest_saved: number;
    total_savings: number;
  };
  reduce_tenure: {
    emi: number;
    new_tenure_months: number;
    months_saved: number;
    new_payoff_date: string;
    interest_saved: number;
    total_savings: number;
  };
}

interface PreclosureResult {
  closure_date: string;
  outstanding_principal: number;
  accrued_interest: number;
  prepayment_penalty: number;
  prepayment_penalty_rate: number;
  total_payoff_amount: number;
  interest_saved: number;
  net_savings: number;
  paid_to_date: {
    principal: number;
    interest: number;
    total: number;
  };
  remaining_emis: number;
}

interface RateChangeResult {
  current_rate: number;
  new_rate: number;
  rate_change: number;
  effective_from: string;
  current_emi: number;
  new_emi: number;
  emi_change: number;
  emi_change_percent: number;
  outstanding_balance: number;
  remaining_months: number;
  current_total_interest: number;
  new_total_interest: number;
  interest_difference: number;
  is_favorable: boolean;
}

export function LoanSimulations({
  accountId,
  currentEmi,
  outstandingBalance,
  currentRate,
  remainingMonths,
}: LoanSimulationsProps) {
  const [earlyPaymentAmount, setEarlyPaymentAmount] = useState('');
  const [earlyPaymentDate, setEarlyPaymentDate] = useState(new Date().toISOString().split('T')[0]);
  const [earlyPaymentResult, setEarlyPaymentResult] = useState<EarlyPaymentResult | null>(null);
  const [earlyPaymentLoading, setEarlyPaymentLoading] = useState(false);

  const [closureDate, setClosureDate] = useState(new Date().toISOString().split('T')[0]);
  const [preclosureResult, setPreclosureResult] = useState<PreclosureResult | null>(null);
  const [preclosureLoading, setPreclosureLoading] = useState(false);

  const [newRate, setNewRate] = useState('');
  const [rateEffectiveDate, setRateEffectiveDate] = useState(new Date().toISOString().split('T')[0]);
  const [rateChangeResult, setRateChangeResult] = useState<RateChangeResult | null>(null);
  const [rateChangeLoading, setRateChangeLoading] = useState(false);

  const [error, setError] = useState('');

  const simulateEarlyPayment = async () => {
    setError('');
    setEarlyPaymentLoading(true);

    try {
      const response = await fetch('/api/v1/loans/simulations/early-payment', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
          'X-Workspace-Id': localStorage.getItem('workspace_id') || '',
        },
        body: JSON.stringify({
          account_id: accountId,
          prepayment_amount: parseFloat(earlyPaymentAmount),
          prepayment_date: earlyPaymentDate,
        }),
      });

      if (!response.ok) throw new Error('Simulation failed');

      const data = await response.json();
      setEarlyPaymentResult(data);
    } catch (err) {
      setError('Failed to simulate early payment');
      console.error(err);
    } finally {
      setEarlyPaymentLoading(false);
    }
  };

  const simulatePreclosure = async () => {
    setError('');
    setPreclosureLoading(true);

    try {
      const response = await fetch('/api/v1/loans/simulations/preclosure', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
          'X-Workspace-Id': localStorage.getItem('workspace_id') || '',
        },
        body: JSON.stringify({
          account_id: accountId,
          closure_date: closureDate,
        }),
      });

      if (!response.ok) throw new Error('Simulation failed');

      const data = await response.json();
      setPreclosureResult(data);
    } catch (err) {
      setError('Failed to simulate pre-closure');
      console.error(err);
    } finally {
      setPreclosureLoading(false);
    }
  };

  const simulateRateChange = async () => {
    setError('');
    setRateChangeLoading(true);

    try {
      const response = await fetch('/api/v1/loans/simulations/interest-rate-change', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
          'X-Workspace-Id': localStorage.getItem('workspace_id') || '',
        },
        body: JSON.stringify({
          account_id: accountId,
          new_interest_rate: parseFloat(newRate),
          effective_from_date: rateEffectiveDate,
        }),
      });

      if (!response.ok) throw new Error('Simulation failed');

      const data = await response.json();
      setRateChangeResult(data);
    } catch (err) {
      setError('Failed to simulate rate change');
      console.error(err);
    } finally {
      setRateChangeLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Tabs defaultValue="early-payment" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="early-payment">
            <Calculator className="h-4 w-4 mr-2" />
            Early Payment
          </TabsTrigger>
          <TabsTrigger value="preclosure">
            <DollarSign className="h-4 w-4 mr-2" />
            Pre-closure
          </TabsTrigger>
          <TabsTrigger value="rate-change">
            <Percent className="h-4 w-4 mr-2" />
            Rate Change
          </TabsTrigger>
        </TabsList>

        {/* Early Payment Simulation */}
        <TabsContent value="early-payment" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Simulate Early Payment</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="earlyPaymentAmount">Prepayment Amount</Label>
                  <Input
                    id="earlyPaymentAmount"
                    type="number"
                    value={earlyPaymentAmount}
                    onChange={(e) => setEarlyPaymentAmount(e.target.value)}
                    placeholder="Enter amount"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="earlyPaymentDate">Payment Date</Label>
                  <Input
                    id="earlyPaymentDate"
                    type="date"
                    value={earlyPaymentDate}
                    onChange={(e) => setEarlyPaymentDate(e.target.value)}
                  />
                </div>
              </div>

              <Button onClick={simulateEarlyPayment} disabled={earlyPaymentLoading || !earlyPaymentAmount}>
                {earlyPaymentLoading ? 'Calculating...' : 'Simulate'}
              </Button>

              {earlyPaymentResult && (
                <div className="mt-6 space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-sm font-medium">Option 1: Reduce EMI</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        <div>
                          <div className="text-2xl font-bold text-green-600">
                            {formatCurrency(earlyPaymentResult.reduce_emi.new_emi)}
                          </div>
                          <p className="text-xs text-muted-foreground">New EMI</p>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span>Reduction:</span>
                          <span className="font-medium">
                            {formatCurrency(earlyPaymentResult.reduce_emi.emi_reduction)} (
                            {earlyPaymentResult.reduce_emi.emi_reduction_percent.toFixed(1)}%)
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span>Interest Saved:</span>
                          <span className="font-medium text-green-600">
                            {formatCurrency(earlyPaymentResult.reduce_emi.interest_saved)}
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span>Tenure:</span>
                          <span>{earlyPaymentResult.reduce_emi.tenure_months} months</span>
                        </div>
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-sm font-medium">Option 2: Reduce Tenure</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        <div>
                          <div className="text-2xl font-bold text-blue-600">
                            {earlyPaymentResult.reduce_tenure.months_saved} months
                          </div>
                          <p className="text-xs text-muted-foreground">Saved</p>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span>New Tenure:</span>
                          <span className="font-medium">{earlyPaymentResult.reduce_tenure.new_tenure_months} months</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span>Interest Saved:</span>
                          <span className="font-medium text-green-600">
                            {formatCurrency(earlyPaymentResult.reduce_tenure.interest_saved)}
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span>EMI:</span>
                          <span>{formatCurrency(earlyPaymentResult.reduce_tenure.emi)}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span>New Payoff:</span>
                          <span>{new Date(earlyPaymentResult.reduce_tenure.new_payoff_date).toLocaleDateString()}</span>
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Pre-closure Simulation */}
        <TabsContent value="preclosure" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Simulate Loan Pre-closure</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="closureDate">Closure Date</Label>
                <Input
                  id="closureDate"
                  type="date"
                  value={closureDate}
                  onChange={(e) => setClosureDate(e.target.value)}
                />
              </div>

              <Button onClick={simulatePreclosure} disabled={preclosureLoading}>
                {preclosureLoading ? 'Calculating...' : 'Calculate Payoff'}
              </Button>

              {preclosureResult && (
                <div className="mt-6 space-y-4">
                  <Card className="bg-blue-50 border-blue-200">
                    <CardContent className="pt-6">
                      <div className="text-center">
                        <div className="text-3xl font-bold text-blue-900">
                          {formatCurrency(preclosureResult.total_payoff_amount)}
                        </div>
                        <p className="text-sm text-blue-700 mt-1">Total Payoff Amount</p>
                      </div>
                    </CardContent>
                  </Card>

                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span>Outstanding Principal:</span>
                        <span className="font-medium">{formatCurrency(preclosureResult.outstanding_principal)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Accrued Interest:</span>
                        <span className="font-medium">{formatCurrency(preclosureResult.accrued_interest)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Prepayment Penalty ({preclosureResult.prepayment_penalty_rate}%):</span>
                        <span className="font-medium">{formatCurrency(preclosureResult.prepayment_penalty)}</span>
                      </div>
                    </div>

                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span>Interest Saved:</span>
                        <span className="font-medium text-green-600">{formatCurrency(preclosureResult.interest_saved)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Net Savings:</span>
                        <span className="font-medium text-green-600">{formatCurrency(preclosureResult.net_savings)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Remaining EMIs:</span>
                        <span className="font-medium">{preclosureResult.remaining_emis}</span>
                      </div>
                    </div>
                  </div>

                  <Alert>
                    <AlertDescription className="text-sm">
                      You have paid {formatCurrency(preclosureResult.paid_to_date.total)} so far (
                      {formatCurrency(preclosureResult.paid_to_date.principal)} principal +
                      {formatCurrency(preclosureResult.paid_to_date.interest)} interest)
                    </AlertDescription>
                  </Alert>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Interest Rate Change Simulation */}
        <TabsContent value="rate-change" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Simulate Interest Rate Change</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="newRate">New Interest Rate (%)</Label>
                  <Input
                    id="newRate"
                    type="number"
                    step="0.01"
                    value={newRate}
                    onChange={(e) => setNewRate(e.target.value)}
                    placeholder={`Current: ${currentRate}%`}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="rateEffectiveDate">Effective From</Label>
                  <Input
                    id="rateEffectiveDate"
                    type="date"
                    value={rateEffectiveDate}
                    onChange={(e) => setRateEffectiveDate(e.target.value)}
                  />
                </div>
              </div>

              <Button onClick={simulateRateChange} disabled={rateChangeLoading || !newRate}>
                {rateChangeLoading ? 'Calculating...' : 'Simulate'}
              </Button>

              {rateChangeResult && (
                <div className="mt-6 space-y-4">
                  <Card className={rateChangeResult.is_favorable ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}>
                    <CardContent className="pt-6">
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="text-sm text-muted-foreground">Rate Change</div>
                          <div className="text-2xl font-bold">
                            {rateChangeResult.current_rate.toFixed(2)}% → {rateChangeResult.new_rate.toFixed(2)}%
                          </div>
                          <div className={`text-sm ${rateChangeResult.is_favorable ? 'text-green-600' : 'text-red-600'}`}>
                            {rateChangeResult.rate_change > 0 ? '+' : ''}
                            {rateChangeResult.rate_change.toFixed(2)}%
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-sm text-muted-foreground">EMI Impact</div>
                          <div className="text-2xl font-bold">
                            {formatCurrency(rateChangeResult.new_emi)}
                          </div>
                          <div className={`text-sm ${rateChangeResult.is_favorable ? 'text-green-600' : 'text-red-600'}`}>
                            {rateChangeResult.emi_change > 0 ? '+' : ''}
                            {formatCurrency(rateChangeResult.emi_change)} ({rateChangeResult.emi_change_percent.toFixed(1)}%)
                          </div>
                        </div>
                      </div>
                    </CardContent>
                  </Card>

                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div className="space-y-2">
                      <div className="flex justify-between">
                        <span>Current EMI:</span>
                        <span className="font-medium">{formatCurrency(rateChangeResult.current_emi)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>New EMI:</span>
                        <span className="font-medium">{formatCurrency(rateChangeResult.new_emi)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Outstanding:</span>
                        <span>{formatCurrency(rateChangeResult.outstanding_balance)}</span>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <div className="flex justify-between">
                        <span>Current Total Interest:</span>
                        <span className="font-medium">{formatCurrency(rateChangeResult.current_total_interest)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>New Total Interest:</span>
                        <span className="font-medium">{formatCurrency(rateChangeResult.new_total_interest)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Difference:</span>
                        <span className={`font-medium ${rateChangeResult.is_favorable ? 'text-green-600' : 'text-red-600'}`}>
                          {rateChangeResult.interest_difference > 0 ? '+' : ''}
                          {formatCurrency(rateChangeResult.interest_difference)}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
