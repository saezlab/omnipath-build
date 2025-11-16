import { useState, useEffect } from 'react';
import { fetchCvTermDetails, type CvTermDetails } from '@/features/cv-terms/api/queries';

export function useCvTerm(cvTermId: string | undefined) {
  const [data, setData] = useState<CvTermDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!cvTermId) {
      setData(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchCvTermDetails(cvTermId)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error('Failed to fetch CV term'));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [cvTermId]);

  return { data, loading, error };
}