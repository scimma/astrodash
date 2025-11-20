from django import forms
from django.core.validators import FileExtensionValidator

class ClassifyForm(forms.Form):
    supernova_name = forms.CharField(
        label="Supernova Name",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. SN1998bw'})
    )

    file = forms.FileField(
        label="Upload Spectrum",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['txt', 'dat', 'ascii', 'csv'])],
        help_text="Upload a spectrum file (text format, two columns: wavelength and flux)"
    )
    
    # Analysis Options
    MODEL_CHOICES = [
        ('dash', 'Dash Model'),
        ('transformer', 'Transformer Model'),
    ]
    model = forms.ChoiceField(
        choices=MODEL_CHOICES, 
        initial='transformer',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    smoothing = forms.IntegerField(
        initial=0,
        min_value=0,
        max_value=20,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    min_wave = forms.IntegerField(
        label="Min Wavelength",
        initial=3500,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    max_wave = forms.IntegerField(
        label="Max Wavelength",
        initial=10000,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    known_z = forms.BooleanField(
        label="Known Redshift",
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    redshift = forms.FloatField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'})
    )

    def clean(self):
        cleaned_data = super().clean()
        file = cleaned_data.get('file')
        supernova_name = cleaned_data.get('supernova_name')
        known_z = cleaned_data.get('known_z')
        redshift = cleaned_data.get('redshift')
        model = cleaned_data.get('model')

        if not file and not supernova_name:
            raise forms.ValidationError("Please provide either a spectrum file or a Supernova Name.")

        if known_z and redshift is None:
            self.add_error('redshift', "Redshift is required when 'Known Redshift' is checked.")
        
        if model == 'transformer' and redshift is None:
             self.add_error('redshift', "Redshift is required for Transformer model.")

        return cleaned_data


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class BatchForm(forms.Form):
    # Support for both zip and multiple files
    zip_file = forms.FileField(
        label="Upload Zip File",
        required=False,
        validators=[FileExtensionValidator(allowed_extensions=['zip'])],
        help_text="Upload a ZIP file containing spectrum files."
    )
    
    files = forms.FileField(
        label="Upload Multiple Files",
        required=False,
        widget=MultipleFileInput(attrs={'multiple': True}),
        help_text="Select multiple spectrum files to upload."
    )

    # Analysis Options (Similar to ClassifyForm)
    model = forms.ChoiceField(
        choices=ClassifyForm.MODEL_CHOICES, 
        initial='dash',
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    smoothing = forms.IntegerField(
        initial=0,
        min_value=0,
        max_value=20,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    min_wave = forms.IntegerField(
        label="Min Wavelength",
        initial=3500,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    max_wave = forms.IntegerField(
        label="Max Wavelength",
        initial=10000,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    
    known_z = forms.BooleanField(
        label="Known Redshift",
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    
    redshift = forms.FloatField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'})
    )

    calculate_rlap = forms.BooleanField(
        label="Calculate RLAP",
        required=False,
        initial=False,
        help_text="Only available for Dash model",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def clean(self):
        cleaned_data = super().clean()
        zip_file = cleaned_data.get('zip_file')
        files = self.files.getlist('files') if hasattr(self, 'files') else []
        
        # Note: In Django forms, file field cleaning for multiple files is tricky 
        # because cleaned_data['files'] might only contain the last file if not handled specifically.
        # We'll handle the 'files' check in the view or assume valid if provided in request.FILES
        
        if not zip_file and not files:
             # This validation might need to be relaxed here and strictly checked in view 
             # or we need to ensure we can access request.FILES len
             pass 

        known_z = cleaned_data.get('known_z')
        redshift = cleaned_data.get('redshift')
        model = cleaned_data.get('model')

        if known_z and redshift is None:
            self.add_error('redshift', "Redshift is required when 'Known Redshift' is checked.")
        
        if model == 'transformer' and redshift is None:
             self.add_error('redshift', "Redshift is required for Transformer model.")

        return cleaned_data
