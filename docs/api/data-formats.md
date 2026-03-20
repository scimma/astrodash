# Data Formats & Schemas

Comprehensive documentation of all data formats, schemas, and structures used by AstroDash API.

## Spectrum File Formats

### 1. FITS (Flexible Image Transport System)

#### File Structure

```text
Header Unit (HDU) 0: Primary HDU
+-- Header keywords (metadata)
+-- No data

Header Unit (HDU) 1: Binary Table Extension
+-- Header keywords
+-- Data table with columns:
    +-- WAVELENGTH (or LAMBDA, WAVE)
    +-- FLUX (or FLUX_DENSITY, F)
    +-- Optional: ERROR, MASK, etc.
```

#### Header Keywords

```python
# Required keywords
SIMPLE  =                    T  # FITS standard
BITPIX  =                    8  # Data type
NAXIS   =                    0  # No image data
EXTEND  =                    T  # Has extensions

# Extension keywords
XTENSION= 'BINTABLE'        # Binary table extension
NAXIS1  =                 24  # Bytes per row
NAXIS2  =               1000  # Number of rows
TFIELDS =                  2  # Number of columns
TTYPE1  = 'WAVELENGTH'      # Column 1 name
TFORM1  = 'D'              # Column 1 format (double)
TUNIT1  = 'Angstrom'       # Column 1 units
TTYPE2  = 'FLUX'           # Column 2 name
TFORM2  = 'D'              # Column 2 format (double)
TUNIT2  = 'erg/s/cm2/Angstrom' # Column 2 units
```

#### Data Format

```python
import astropy.io.fits as fits

# Read FITS file
hdul = fits.open('spectrum.fits')

# Get data from first extension
data = hdul[1].data
wavelengths = data['WAVELENGTH']  # or data['LAMBDA']
fluxes = data['FLUX']

# Get metadata
header = hdul[1].header
redshift = header.get('REDSHIFT', 0.0)
object_name = header.get('OBJECT', 'Unknown')
```

#### Example FITS File

```python
import numpy as np
import astropy.io.fits as fits
from astropy.table import Table

# Generate sample data
wavelengths = np.linspace(3500, 10000, 1000)
fluxes = np.random.normal(1.0, 0.1, 1000)

# Create table
table = Table([wavelengths, fluxes], names=['WAVELENGTH', 'FLUX'])

# Create HDU
hdu = fits.BinTableHDU(table)

# Add metadata
hdu.header['OBJECT'] = 'SN2023abc'
hdu.header['REDSHIFT'] = 0.05
hdu.header['TELESCOP'] = 'HST'

# Write file
hdu.writeto('sample_spectrum.fits', overwrite=True)
print('Sample FITS file created')
```

### 2. DAT (ASCII Data)

#### File Structure

```text
# Optional header lines starting with #
# Data columns separated by whitespace
# Wavelength  Flux  Error(optional)
3500.0      1.234  0.123
3501.0      1.245  0.124
3502.0      1.256  0.125
...
```

#### Format Specifications

- **Delimiter**: Whitespace (spaces or tabs)
- **Header**: Lines starting with `#` are ignored
- **Columns**: Minimum 2 columns (wavelength, flux)
- **Optional**: Third column for flux errors
- **Units**: Wavelength in Angstroms, flux in erg/s/cm2/A

#### Example DAT File

```bash
# Create sample DAT file
cat > sample_spectrum.dat << 'EOF'
# Sample spectrum data
# Wavelength(Angstrom)  Flux(erg/s/cm2/Angstrom)  Error
3500.0      1.234  0.123
3501.0      1.245  0.124
3502.0      1.256  0.125
3503.0      1.267  0.126
3504.0      1.278  0.127
EOF
```

### 3. TXT (Text Format)

#### File Structure

Similar to DAT format but more flexible:

```text
# Header information
# Object: SN2023abc
# Redshift: 0.05
# Telescope: HST
# Date: 2023-12-10
# Columns: Wavelength Flux Error
3500.0      1.234  0.123
3501.0      1.245  0.124
...
```

#### Format Variations

- **Space-separated**: `3500.0 1.234 0.123`
- **Tab-separated**: `3500.0\t1.234\t0.123`
- **Comma-separated**: `3500.0,1.234,0.123`

### 4. LNW (Line List Format)

#### File Structure

```text
# Line list format
# Wavelength  Flux  Line_Type
3500.0      1.234  emission
3501.0      1.245  absorption
3502.0      1.256  emission
...
```

#### Special Handling

- **Wavelength Range**: 3500-10000 A (based on DashSpectrumProcessor configuration)
- **Line Types**: emission, absorption, continuum
- **Processing**: Special handling for line identification

## Template Data Structures

### 1. Template Response Schema

#### JSON Structure

```json
{
  "x": [3500.0, 3501.0, 3502.0, "..."],
  "y": [1.234, 1.245, 1.256, "..."]
}
```

#### Data Types

```python
# Template data types
template_data = {
    "x": List[float],           # Wavelength values (A)
    "y": List[float]            # Flux values (erg/s/cm2/A)
}
```

### 2. Template File Format

#### Internal Storage

Templates are stored as compressed NumPy arrays:

```python
# Template file structure
template_file = {
    "wavelengths": np.array([3500.0, 3501.0, ...]),  # A
    "fluxes": np.array([1.234, 1.245, ...]),         # erg/s/cm2/A
    "metadata": {
        "sn_type": "Ia",
        "age_bin": "2 to 6",
        "redshift": 0.0
    }
}

# Save template
np.savez_compressed(
    'template_Ia_2to6.npz',
    wavelengths=wavelengths,
    fluxes=fluxes,
    metadata=metadata
)
```

## Model Output Formats

### 1. Classification Response Schema

#### Complete Response from `/process` Endpoint

```json
{
  "spectrum": {
    "x": [3500.0, 3501.0, 3502.0, "..."],
    "y": [1.234, 1.245, 1.256, "..."],
    "redshift": 0.05
  },
  "classification": {
    "best_matches": [
      {
        "type": "Ia-norm",
        "confidence": 0.95,
        "age_bin": "4 to 8"
      },
      {
        "type": "Ia-91T",
        "confidence": 0.03,
        "age_bin": "2 to 6"
      }
    ],
    "model_type": "dash_classifier"
  },
  "model_type": "dash"
}
```

#### Response Components

##### Spectrum Data

```python
spectrum_data = {
    "x": List[float],           # Wavelength array (A)
    "y": List[float],           # Flux array (erg/s/cm2/A)
    "redshift": Optional[float] # Applied redshift
}
```

##### Classification Results

```python
classification_data = {
    "best_matches": List[Dict], # List of classification matches
    "model_type": str           # Type of classifier used
}
```

##### Individual Match

```python
match_data = {
    "type": str,                # Supernova type
    "confidence": float,        # Confidence score
    "age_bin": str              # Age classification
}
```

### 2. Model-Specific Outputs

#### Dash Classifier

```python
# Dash CNN output
dash_output = {
    "type": "Ia-norm",
    "confidence": 0.95,
    "age_bin": "4 to 8",
    "model_used": "dash_classifier"
}
```

#### Transformer Classifier

```python
# Transformer output
transformer_output = {
    "type": "Ia-norm",
    "confidence": 0.93,
    "age_bin": "4 to 8",
    "model_used": "transformer_classifier"
}
```

#### User-Uploaded Model

```python
# Custom model output
custom_output = {
    "type": "custom_type",
    "confidence": 0.87,
    "age_bin": "unknown",
    "model_used": "user_model_123",
    "model_id": "abc123def456"
}
```

## Batch Processing Results

### 1. Batch Response Schema

#### ZIP File Processing

```json
{
  "spectrum1.fits": {
    "spectrum": {
      "x": [3500.0, 3501.0, "..."],
      "y": [1.234, 1.245, "..."]
    },
    "classification": {
      "best_matches": [
        {
          "type": "Ia-norm",
          "confidence": 0.95
        }
      ]
    }
  },
  "spectrum2.dat": {
    "spectrum": {
      "x": [3500.0, 3501.0, "..."],
      "y": [1.345, 1.356, "..."]
    },
    "classification": {
      "best_matches": [
        {
          "type": "Ib",
          "confidence": 0.87
        }
      ]
    }
  },
  "corrupted_file.xyz": {
    "error": "Unsupported file type"
  }
}
```

#### Multiple File Processing

```json
{
  "file1.fits": {
    "spectrum": {},
    "classification": {}
  },
  "file2.dat": {
    "spectrum": {},
    "classification": {}
  },
  "file3.txt": {
    "error": "No valid spectrum data found"
  }
}
```

### 2. Batch Processing Metadata

#### Processing Summary

```python
batch_summary = {
    "total_files": 100,
    "successful": 95,
    "failed": 5,
    "processing_time": 45.67,
    "start_time": "2023-12-10T14:30:00Z",
    "end_time": "2023-12-10T14:30:45Z",
    "errors": [
        {
            "file": "file3.txt",
            "error": "No valid spectrum data found",
            "error_type": "ValidationError"
        }
    ]
}
```

## Request/Response Headers

### 1. Standard Headers

#### Request Headers

```text
Content-Type: multipart/form-data
User-Agent: AstroDash-Client/1.0
Accept: application/json
```

#### Response Headers

```text
Content-Type: application/json
```

**Note**: The API does not provide `X-RateLimit-*` or `X-Processing-Time` headers. Rate limiting is handled via HTTP 429 status codes with `Retry-After` headers.

### 2. Custom Headers

## Data Validation Rules

### 1. Input Validation

#### File Validation

```python
FILE_VALIDATION_RULES = {
    "max_size": 50 * 1024 * 1024,  # 50MB (from settings)
    "allowed_extensions": [".fits", ".dat", ".txt", ".lnw", ".csv"],
    "min_wavelength": 3500.0,       # A (DashSpectrumProcessor w0)
    "max_wavelength": 10000.0,      # A (DashSpectrumProcessor w1)
    "min_data_points": 100,
    "max_data_points": 100000
}
```

#### Parameter Validation

```python
PARAMETER_VALIDATION_RULES = {
    "smoothing": {
        "type": int,
        "min": 0,
        "max": 10,
        "default": 0
    },
    "z_value": {
        "type": float,
        "min": 0.0,
        "max": 10.0,
        "required_if": "knownZ"
    },
    "min_wave": {
        "type": float,
        "min": 1000.0,
        "max": 50000.0
    }
}
```

### 2. Output Validation

#### Response Validation

```python
RESPONSE_VALIDATION_RULES = {
    "required_fields": ["spectrum", "classification"],
    "spectrum_fields": ["x", "y"],
    "classification_fields": ["best_matches"],
    "data_types": {
        "x": "array[float]",
        "y": "array[float]",
        "confidence": "float[0,1]"
    }
}
```

## Data Conversion Utilities

### 1. Format Conversion

#### Python Utilities

```python
import numpy as np
import astropy.io.fits as fits
from astropy.table import Table

def convert_fits_to_dat(fits_file, dat_file):
    """Convert FITS file to DAT format."""
    hdul = fits.open(fits_file)
    data = hdul[1].data

    wavelengths = data['WAVELENGTH']
    fluxes = data['FLUX']

    # Write DAT file
    with open(dat_file, 'w') as fh:
        fh.write("# Wavelength(Angstrom)  Flux(erg/s/cm2/Angstrom)\n")
        for w, flx in zip(wavelengths, fluxes):
            fh.write(f"{w:.3f}  {flx:.6f}\n")

def convert_dat_to_csv(dat_file, csv_file):
    """Convert DAT file to CSV format."""
    data = np.loadtxt(dat_file, skiprows=1)

    import csv
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Wavelength', 'Flux'])
        for row in data:
            writer.writerow(row)
```

#### Command Line Tools

```bash
# Convert FITS to CSV using astropy
python -c "
import astropy.io.fits as fits
import pandas as pd

hdul = fits.open('spectrum.fits')
data = hdul[1].data
df = pd.DataFrame(data)
df.to_csv('spectrum.csv', index=False)
print('Converted to CSV')
"

# Convert DAT to CSV using awk
awk '!/^#/ {print $1 "," $2}' spectrum.dat > spectrum.csv
```

### 2. Data Quality Checks

#### Validation Functions

```python
def validate_spectrum_data(wavelengths, fluxes):
    """Validate spectrum data quality."""
    errors = []

    # Check data length
    if len(wavelengths) != len(fluxes):
        errors.append("Wavelength and flux arrays have different lengths")

    # Check wavelength ordering
    if not np.all(np.diff(wavelengths) > 0):
        errors.append("Wavelengths are not monotonically increasing")

    # Check for NaN values
    if np.any(np.isnan(wavelengths)) or np.any(np.isnan(fluxes)):
        errors.append("Data contains NaN values")

    # Check wavelength range
    if np.min(wavelengths) < 1000 or np.max(wavelengths) > 50000:
        errors.append("Wavelengths outside valid range (1000-50000 A)")

    return errors

def normalize_spectrum(wavelengths, fluxes):
    """Normalize spectrum to unit flux."""
    # Remove continuum
    continuum = np.median(fluxes)
    normalized = fluxes - continuum

    # Normalize to unit area
    area = np.trapz(normalized, wavelengths)
    normalized = normalized / area

    return normalized
```

## Best Practices

### 1. File Preparation

- Use consistent wavelength units (Angstroms)
- Ensure wavelength arrays are monotonically increasing
- Remove or flag bad data points
- Provide appropriate flux units
- Include metadata when possible

### 2. Data Quality

- Validate data before upload
- Check for NaN or infinite values
- Ensure sufficient spectral coverage
- Verify wavelength calibration
- Test with known good files

### 3. Performance Considerations

- Use appropriate file formats for your use case
- Compress large files when possible
- Batch process multiple files
- Cache frequently used data
- Monitor processing times
