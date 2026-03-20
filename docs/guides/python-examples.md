# Python Examples (v1 API)

Complete Python examples for using the AstroDASH 2.0 API.

## Setup

First, install the required dependencies:

```bash
pip install requests numpy matplotlib
```

## Basic Spectrum Processing

### Process a Single Spectrum

```python
import requests
import json
import matplotlib.pyplot as plt

def process_spectrum(file_path, smoothing=6, known_z=False, z_value=None):
    """
    Process a spectrum file and get classification results.
    """
    files = {'file': open(file_path, 'rb')}
    params = {
        'smoothing': smoothing,
        'knownZ': known_z
    }

    if known_z and z_value is not None:
        params['zValue'] = z_value

    data = {'params': json.dumps(params)}

    response = requests.post('http://localhost:8000/api/v1/process',
                           files=files, data=data)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Example usage
result = process_spectrum(
    file_path="spectrum.fits",
    smoothing=6,
    known_z=True,
    z_value=0.5
)

if result:
    print(f"Top classification: {result['classification']['best_matches'][0]['type']}")
    print(f"Confidence: {result['classification']['best_matches'][0]['confidence']:.2f}")
    print(f"Age bin: {result['classification']['best_matches'][0]['age_bin']}")
```

### Plot the Results

```python
def plot_spectrum_result(result):
    """
    Plot the processed spectrum with classification results.
    """
    spectrum = result['spectrum']
    classification = result['classification']

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    # Plot spectrum
    ax1.plot(spectrum['x'], spectrum['y'], 'b-', linewidth=1)
    ax1.set_xlabel('Wavelength (A)')
    ax1.set_ylabel('Flux')
    ax1.set_title('Processed Spectrum')
    ax1.grid(True, alpha=0.3)

    # Plot classification results
    matches = classification['best_matches']
    types = [match['type'] for match in matches]
    confidences = [match['confidence'] for match in matches]

    bars = ax2.bar(types, confidences, color='skyblue')
    ax2.set_xlabel('Supernova Type')
    ax2.set_ylabel('Confidence')
    ax2.set_title('Classification Results')
    ax2.tick_params(axis='x', rotation=45)

    # Highlight top match
    top_match = classification['best_matches'][0]['type']
    for bar, t in zip(bars, types):
        if t == top_match:
            bar.set_color('red')

    plt.tight_layout()
    plt.show()

# Use the function
if result:
    plot_spectrum_result(result)
```

## Using OSC References

### Process OSC Reference

```python
def process_osc_reference(osc_ref, smoothing=6):
    """
    Process a spectrum using an OSC reference.
    """
    params = {
        'oscRef': osc_ref,
        'smoothing': smoothing
    }

    data = {'params': json.dumps(params)}

    response = requests.post('http://localhost:8000/api/v1/process', data=data)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Example usage
result = process_osc_reference("osc-sn2011fe-0", smoothing=4)
if result:
    print(f"OSC spectrum classified as: {result['classification']['best_matches'][0]['type']}")
    print(f"Confidence: {result['classification']['best_matches'][0]['confidence']:.2f}")
    print(f"Age bin: {result['classification']['best_matches'][0]['age_bin']}")
```

## Batch Processing

### Process Multiple Files

```python
import zipfile
import os

def create_spectra_zip(spectrum_files, zip_path="spectra.zip"):
    """
    Create a ZIP file containing spectrum files for batch processing.
    """
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for file_path in spectrum_files:
            zipf.write(file_path, os.path.basename(file_path))
    return zip_path

def batch_process_spectra(zip_path, smoothing=6):
    """
    Process multiple spectra using batch endpoint.
    """
    files = {'zip_file': open(zip_path, 'rb')}
    data = {'params': json.dumps({'smoothing': smoothing})}

    response = requests.post('http://localhost:8000/api/v1/batch-process',
                           files=files, data=data)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Example usage
spectrum_files = ["spectrum1.fits", "spectrum2.fits", "spectrum3.fits"]
zip_path = create_spectra_zip(spectrum_files)
results = batch_process_spectra(zip_path, smoothing=6)

if results:
    for filename, result in results.items():
        if 'error' not in result:
            print(f"{filename}: {result['classification']['best_matches'][0]['type']}")
            print(f"  Confidence: {result['classification']['best_matches'][0]['confidence']:.2f}")
            print(f"  Age bin: {result['classification']['best_matches'][0]['age_bin']}")
        else:
            print(f"{filename}: Error - {result['error']}")
```

## Template Spectra

### Get Template Spectrum

```python
def get_template_spectrum(sn_type="Ia-norm", age_bin="4 to 8"):
    """
    Get a template spectrum for a specific SN type and age bin.
    """
    params = {
        'sn_type': sn_type,
        'age_bin': age_bin
    }

    response = requests.get('http://localhost:8000/api/v1/template-spectrum',
                          params=params)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Example usage
template = get_template_spectrum("Ia-norm", "4 to 8")
if template:
    print(f"Template: {template['sn_type']} at {template['age_bin']}")
    print(f"Wavelength range: {template['wave'][0]:.1f} - {template['wave'][-1]:.1f} A")
```

### Compare with Template

```python
def compare_with_template(spectrum_result, template_sn_type="Ia-norm", template_age="4 to 8"):
    """
    Compare a processed spectrum with a template.
    """
    template = get_template_spectrum(template_sn_type, template_age)

    if template and spectrum_result:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

        # Plot spectrum
        spectrum = spectrum_result['spectrum']
        ax1.plot(spectrum['x'], spectrum['y'], 'b-', label='Input Spectrum', linewidth=1)
        ax1.plot(template['wave'], template['flux'], 'r--', label=f'Template: {template_sn_type}', linewidth=1)
        ax1.set_xlabel('Wavelength (A)')
        ax1.set_ylabel('Flux')
        ax1.set_title('Spectrum Comparison')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Plot difference
        # Interpolate template to match spectrum wavelengths
        import numpy as np
        template_interp = np.interp(spectrum['x'], template['wave'], template['flux'])
        difference = spectrum['y'] - template_interp

        ax2.plot(spectrum['x'], difference, 'g-', linewidth=1)
        ax2.set_xlabel('Wavelength (A)')
        ax2.set_ylabel('Difference')
        ax2.set_title('Spectrum - Template')
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.show()

# Example usage
if result and template:
    compare_with_template(result, "Ia-norm", "4 to 8")
```

## Redshift Estimation

### Estimate Redshift

```python
def estimate_redshift_from_file(file_path, sn_type="Ia", age_bin="4 to 8"):
    """
    Estimate redshift using a spectrum file via the redshift endpoint.
    """
    files = {'file': open(file_path, 'rb')}
    data = {
        'sn_type': sn_type,
        'age_bin': age_bin
    }

    response = requests.post('http://localhost:8000/api/v1/estimate-redshift',
                             files=files, data=data)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Example usage
redshift_result = estimate_redshift_from_file("spectrum.fits", "Ia", "4 to 8")
if redshift_result:
    print(f"Estimated redshift: {redshift_result['estimated_redshift']:.3f}")
    print(f"Redshift error: {redshift_result['estimated_redshift_error']:.3f}")
```

## Analysis Options

### Get Available Options

```python
def get_analysis_options():
    """
    Get available SN types and age bins.
    """
    response = requests.get('http://localhost:8000/api/v1/analysis-options')

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None

# Example usage
options = get_analysis_options()
if options:
    print("Available SN types:")
    for sn_type in options['sn_types']:
        print(f"  - {sn_type}")

    print("\nAge bins by type:")
    for sn_type, age_bins in options['age_bins_by_type'].items():
        print(f"  {sn_type}: {age_bins}")
```

## Complete Example Script

Here's a complete script that demonstrates all major features:

```python
import requests
import json
import matplotlib.pyplot as plt
import numpy as np

class AstrodashAPI:
    def __init__(self, base_url="http://localhost:8000"):
        self.base_url = base_url

    def health_check(self):
        """Check API health."""
        response = requests.get(f"{self.base_url}/health")
        return response.json() if response.status_code == 200 else None

    def process_spectrum(self, file_path, **params):
        """Process a spectrum file."""
        files = {'file': open(file_path, 'rb')}
        data = {'params': json.dumps(params)}

        response = requests.post(f"{self.base_url}/api/v1/process", files=files, data=data)
        return response.json() if response.status_code == 200 else None

    def get_template(self, sn_type, age_bin):
        """Get template spectrum."""
        params = {'sn_type': sn_type, 'age_bin': age_bin}
        response = requests.get(f"{self.base_url}/api/v1/template-spectrum", params=params)
        return response.json() if response.status_code == 200 else None

    def estimate_redshift(self, wavelengths, fluxes, sn_type, age):
        """Estimate redshift."""
        data = {
            'x': wavelengths.tolist() if hasattr(wavelengths, 'tolist') else wavelengths,
            'y': fluxes.tolist() if hasattr(fluxes, 'tolist') else fluxes,
            'type': sn_type,
            'age': age
        }
        response = requests.post(f"{self.base_url}/api/v1/estimate-redshift", json=data)
        return response.json() if response.status_code == 200 else None

# Usage example
if __name__ == "__main__":
    api = AstrodashAPI()

    # Check health
    health = api.health_check()
    print(f"API Health: {health}")

    # Process a spectrum (if you have a file)
    # result = api.process_spectrum("spectrum.fits", smoothing=6, knownZ=True, zValue=0.5)
    # if result:
    #     print(f"Classification: {result['classification']['top_match']}")

    # Get template
    template = api.get_template("Ia-norm", "4 to 8")
    if template:
        print(f"Template loaded: {template['sn_type']} at {template['age_bin']}")
```

## Error Handling

```python
def safe_api_call(func, *args, **kwargs):
    """
    Wrapper for safe API calls with error handling.
    """
    try:
        result = func(*args, **kwargs)
        if result is None:
            print("API call failed")
            return None
        return result
    except requests.exceptions.ConnectionError:
        print("Could not connect to API. Is the server running?")
        return None
    except requests.exceptions.Timeout:
        print("Request timed out")
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None

# Example usage
result = safe_api_call(process_spectrum, "spectrum.fits")
```
